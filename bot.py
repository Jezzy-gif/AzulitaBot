import asyncio
import logging
import os
import shutil
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")
FFMPEG_PATH = shutil.which("ffmpeg") or "ffmpeg"

COOKIES_TEXT = os.getenv("YOUTUBE_COOKIES", "")
COOKIES_FILE = "/tmp/youtube_cookies.txt"

if COOKIES_TEXT.strip():
    try:
        with open(COOKIES_FILE, "w", encoding="utf-8") as f:
            f.write(COOKIES_TEXT)

        os.chmod(COOKIES_FILE, 0o600)
        print("🍪 Cookies de YouTube cargadas.")
    except Exception as e:
        print(f"⚠️ No se pudieron guardar las cookies: {e}")


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("Azulita")


# ============================================================
# INTENTS
# ============================================================

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True


# ============================================================
# BOT
# ============================================================

class MusicBot(commands.Bot):

    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents
        )

        # guild_id -> lista de canciones
        self.queues = defaultdict(list)

        # guild_id -> tarea que reproduce la cola
        self.queue_tasks = {}

        # guild_id -> lock de voz
        self.voice_locks = defaultdict(asyncio.Lock)

        # guild_id -> canal de voz
        self.join_channels = {}


bot = MusicBot()


# ============================================================
# YT-DLP
# ============================================================

YTDLP_BASE = {
    "format": "bestaudio/best",
    "noplaylist": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
    "skip_download": True,
    "retries": 3,
    "fragment_retries": 3,
    "socket_timeout": 20,
    "geo_bypass": True,
}

if os.path.isfile(COOKIES_FILE):
    YTDLP_BASE["cookiefile"] = COOKIES_FILE


# ============================================================
# EXTRAER INFORMACIÓN
# ============================================================

async def yt_extract(query, playlist=False):

    options = YTDLP_BASE.copy()
    options["noplaylist"] = not playlist

    loop = asyncio.get_running_loop()

    def extract():
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(
                query,
                download=False
            )

    return await loop.run_in_executor(None, extract)


# ============================================================
# BUSCAR / OBTENER CANCIONES
# ============================================================

async def get_tracks(query):

    try:

        # Si es playlist de YouTube
        if "playlist" in query.lower() or "list=" in query:

            data = await yt_extract(
                query,
                playlist=True
            )

            if not data:
                raise RuntimeError(
                    "YouTube no devolvió información."
                )

            entries = data.get("entries") or []

            tracks = []

            for entry in entries:

                if not entry:
                    continue

                webpage = entry.get("webpage_url")

                if not webpage:
                    webpage = entry.get("url")

                if not webpage:
                    continue

                tracks.append({
                    "title": entry.get(
                        "title",
                        "Canción desconocida"
                    ),
                    "url": webpage
                })

            if not tracks:
                raise RuntimeError(
                    "La playlist no contiene canciones válidas."
                )

            return tracks

        # Canción normal / búsqueda
        data = await yt_extract(
            query,
            playlist=False
        )

        if not data:
            raise RuntimeError(
                "YouTube no devolvió información."
            )

        if "entries" in data:

            entries = data.get("entries") or []

            if not entries:
                raise RuntimeError(
                    "No se encontró la canción."
                )

            data = entries[0]

        webpage = data.get("webpage_url")

        if not webpage:
            webpage = query

        return [{
            "title": data.get(
                "title",
                "Canción desconocida"
            ),
            "url": webpage
        }]

    except Exception as e:

        log.exception(
            "Error obteniendo canciones"
        )

        raise RuntimeError(
            f"No se pudo obtener la canción o playlist.\n\n{e}"
        )


# ============================================================
# OBTENER URL DE AUDIO
# ============================================================

async def get_audio_url(webpage):

    """
    IMPORTANTE:
    Se vuelve a ejecutar yt-dlp cada vez que comienza
    una canción. Así evitamos usar URLs de audio caducadas.
    """

    try:

        data = await yt_extract(
            webpage,
            playlist=False
        )

        if not data:
            raise RuntimeError(
                "YouTube no devolvió información."
            )

        if "entries" in data:

            entries = data.get("entries") or []

            if not entries:
                raise RuntimeError(
                    "No se encontró el audio."
                )

            data = entries[0]

        audio_url = data.get("url")

        if not audio_url:
            raise RuntimeError(
                "YouTube no entregó una URL de audio."
            )

        return {
            "audio_url": audio_url,
            "title": data.get(
                "title",
                "Canción desconocida"
            ),
            "webpage": data.get(
                "webpage_url",
                webpage
            )
        }

    except Exception as e:

        log.exception(
            "Error extrayendo audio"
        )

        raise RuntimeError(str(e))


# ============================================================
# VOZ
# ============================================================

async def connect_voice(guild, channel):

    async with bot.voice_locks[guild.id]:

        vc = guild.voice_client

        if vc and vc.is_connected():

            if vc.channel and vc.channel.id == channel.id:
                return vc

            try:
                await vc.move_to(channel)
                return vc

            except Exception:
                pass

        if vc:

            try:
                await vc.disconnect(force=True)
            except Exception:
                pass

            await asyncio.sleep(1)

        try:

            vc = await channel.connect(
                reconnect=True,
                timeout=30
            )

            bot.join_channels[guild.id] = channel.id

            log.info(
                "Conectado a voz: %s",
                channel.name
            )

            return vc

        except Exception as e:

            raise RuntimeError(
                f"No pude conectarme al canal: {e}"
            )


# ============================================================
# FFmpeg
# ============================================================

def create_source(audio_url):

    return discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(
            audio_url,
            executable=FFMPEG_PATH,
            before_options=(
                "-reconnect 1 "
                "-reconnect_streamed 1 "
                "-reconnect_delay_max 5"
            ),
            options=(
                "-vn "
                "-loglevel warning "
                "-ac 2 "
                "-ar 48000"
            )
        ),
        volume=0.7
    )


# ============================================================
# REPRODUCIR COLA
# ============================================================

async def play_queue(guild):

    guild_id = guild.id

    try:

        while bot.queues[guild_id]:

            item = bot.queues[guild_id][0]

            vc = guild.voice_client

            if not vc or not vc.is_connected():

                channel_id = bot.join_channels.get(guild_id)

                if not channel_id:
                    log.warning(
                        "No hay canal guardado para %s",
                        guild.name
                    )
                    break

                channel = guild.get_channel(channel_id)

                if not channel:
                    break

                vc = await connect_voice(
                    guild,
                    channel
                )

            # =================================================
            # RE-EXTRAER AUDIO
            # =================================================

            try:

                log.info(
                    "Extrayendo: %s",
                    item["title"]
                )

                info = await get_audio_url(
                    item["url"]
                )

                audio_url = info["audio_url"]

                title = info["title"]

                item["title"] = title

            except Exception as e:

                log.error(
                    "No se pudo reproducir %s: %s",
                    item["title"],
                    e
                )

                # Sacamos la canción que falló
                bot.queues[guild_id].pop(0)

                # Continuamos con la siguiente
                continue

            # =================================================
            # CREAR AUDIO
            # =================================================

            finished = asyncio.Event()
            playback_error = None

            def after(error):

                nonlocal playback_error

                playback_error = error

                try:
                    loop = asyncio.get_running_loop()
                    loop.call_soon_threadsafe(
                        finished.set
                    )
                except RuntimeError:
                    pass

            try:

                source = create_source(
                    audio_url
                )

                vc.play(
                    source,
                    after=after
                )

            except Exception as e:

                log.exception(
                    "Error iniciando FFmpeg"
                )

                bot.queues[guild_id].pop(0)

                continue

            log.info(
                "▶️ Reproduciendo: %s",
                title
            )

            # =================================================
            # ESPERAR FIN
            # =================================================

            await finished.wait()

            if playback_error:

                log.error(
                    "Error reproduciendo %s: %s",
                    title,
                    playback_error
                )

            # =================================================
            # QUITAR DE LA COLA
            # =================================================

            if bot.queues[guild_id]:

                bot.queues[guild_id].pop(0)

        log.info(
            "Cola terminada en %s",
            guild.name
        )

    except asyncio.CancelledError:

        log.info(
            "Cola cancelada en %s",
            guild.name
        )

    except Exception:

        log.exception(
            "Error en reproductor de cola"
        )

    finally:

        bot.queue_tasks.pop(
            guild_id,
            None
        )


# ============================================================
# INICIAR COLA
# ============================================================

def start_queue(guild):

    task = bot.queue_tasks.get(guild.id)

    if task and not task.done():
        return

    bot.queue_tasks[guild.id] = asyncio.create_task(
        play_queue(guild)
    )


# ============================================================
# SETUP
# ============================================================

@bot.event
async def on_ready():

    try:

        synced = await bot.tree.sync()

        log.info(
            "======================================"
        )

        log.info(
            "BOT CONECTADO: %s",
            bot.user
        )

        log.info(
            "Comandos sincronizados: %s",
            len(synced)
        )

        log.info(
            "FFmpeg: %s",
            FFMPEG_PATH
        )

        log.info(
            "======================================"
        )

    except Exception:

        log.exception(
            "Error en on_ready"
        )


# ============================================================
# /JOIN
# ============================================================

@bot.tree.command(
    name="join",
    description="Entra a tu canal de voz."
)
async def join(interaction):

    await interaction.response.defer()

    if not interaction.guild:
        return await interaction.followup.send(
            "❌ Este comando solo funciona en servidores."
        )

    member = interaction.user

    if not member.voice or not member.voice.channel:

        return await interaction.followup.send(
            "❌ Primero entra a un canal de voz."
        )

    try:

        await connect_voice(
            interaction.guild,
            member.voice.channel
        )

        await interaction.followup.send(
            f"🔊 Conectado a **{member.voice.channel.name}**."
        )

    except Exception as e:

        await interaction.followup.send(
            f"❌ {e}"
        )


# ============================================================
# /PLAY
# ============================================================

@bot.tree.command(
    name="play",
    description="Reproduce una canción o playlist."
)
@app_commands.describe(
    query="Link de YouTube, playlist o búsqueda"
)
async def play(interaction, query: str):

    await interaction.response.defer()

    guild = interaction.guild

    if not guild:
        return await interaction.followup.send(
            "❌ Solo funciona en servidores."
        )

    member = interaction.user

    if not member.voice or not member.voice.channel:

        return await interaction.followup.send(
            "❌ Entra primero a un canal de voz."
        )

    try:

        await connect_voice(
            guild,
            member.voice.channel
        )

        await interaction.followup.send(
            "🔎 Buscando..."
        )

        tracks = await get_tracks(query)

        was_empty = not bot.queues[guild.id]

        bot.queues[guild.id].extend(tracks)

        if len(tracks) == 1:

            msg = (
                f"🎵 Añadido a la cola: "
                f"**{tracks[0]['title']}**"
            )

        else:

            msg = (
                f"📋 Playlist añadida.\n"
                f"🎵 Canciones: **{len(tracks)}**"
            )

        await interaction.channel.send(msg)

        # Si no estaba reproduciendo, arrancamos
        if was_empty:

            start_queue(guild)

    except Exception as e:

        log.exception(
            "Error en /play"
        )

        await interaction.channel.send(
            f"❌ **Error:**\n`{e}`"
        )


# ============================================================
# /ADD
# ============================================================

@bot.tree.command(
    name="add",
    description="Agrega una canción o playlist a la cola."
)
@app_commands.describe(
    query="Link de YouTube, playlist o búsqueda"
)
async def add(interaction, query: str):

    await interaction.response.defer()

    guild = interaction.guild

    if not guild:
        return await interaction.followup.send(
            "❌ Solo funciona en servidores."
        )

    member = interaction.user

    if not member.voice or not member.voice.channel:

        return await interaction.followup.send(
            "❌ Entra primero a un canal de voz."
        )

    try:

        await connect_voice(
            guild,
            member.voice.channel
        )

        await interaction.followup.send(
            "🔎 Agregando a la cola..."
        )

        tracks = await get_tracks(query)

        bot.queues[guild.id].extend(tracks)

        await interaction.channel.send(
            f"✅ Se agregaron **{len(tracks)}** "
            f"canciones a la cola."
        )

        start_queue(guild)

    except Exception as e:

        await interaction.channel.send(
            f"❌ **Error:**\n`{e}`"
        )


# ============================================================
# /QUEUE
# ============================================================

@bot.tree.command(
    name="queue",
    description="Muestra la cola."
)
async def queue(interaction):

    guild = interaction.guild

    if not guild:
        return await interaction.response.send_message(
            "❌ Solo funciona en servidores."
        )

    songs = bot.queues[guild.id]

    if not songs:

        return await interaction.response.send_message(
            "📭 La cola está vacía."
        )

    lines = []

    for i, song in enumerate(songs[:20], 1):

        lines.append(
            f"**{i}.** {song['title']}"
        )

    more = ""

    if len(songs) > 20:

        more = (
            f"\n\n... y "
            f"**{len(songs) - 20}** más."
        )

    await interaction.response.send_message(
        "🎵 **COLA**\n\n"
        + "\n".join(lines)
        + more
    )


# ============================================================
# /SKIP
# ============================================================

@bot.tree.command(
    name="skip",
    description="Salta la canción actual."
)
async def skip(interaction):

    guild = interaction.guild

    if not guild:
        return await interaction.response.send_message(
            "❌ Solo funciona en servidores."
        )

    vc = guild.voice_client

    if not vc or not vc.is_playing():

        return await interaction.response.send_message(
            "❌ No hay ninguna canción reproduciéndose."
        )

    vc.stop()

    await interaction.response.send_message(
        "⏭️ Canción saltada."
    )


# ============================================================
# /PAUSE
# ============================================================

@bot.tree.command(
    name="pause",
    description="Pausa la música."
)
async def pause(interaction):

    vc = (
        interaction.guild.voice_client
        if interaction.guild
        else None
    )

    if not vc or not vc.is_playing():

        return await interaction.response.send_message(
            "❌ No hay música reproduciéndose."
        )

    vc.pause()

    await interaction.response.send_message(
        "⏸️ Música pausada."
    )


# ============================================================
# /RESUME
# ============================================================

@bot.tree.command(
    name="resume",
    description="Reanuda la música."
)
async def resume(interaction):

    vc = (
        interaction.guild.voice_client
        if interaction.guild
        else None
    )

    if not vc or not vc.is_paused():

        return await interaction.response.send_message(
            "❌ La música no está pausada."
        )

    vc.resume()

    await interaction.response.send_message(
        "▶️ Música reanudada."
    )


# ============================================================
# /STOP
# ============================================================

@bot.tree.command(
    name="stop",
    description="Detiene la música."
)
async def stop(interaction):

    guild = interaction.guild

    if not guild:
        return await interaction.response.send_message(
            "❌ Solo funciona en servidores."
        )

    vc = guild.voice_client

    if not vc:

        return await interaction.response.send_message(
            "❌ No estoy conectado."
        )

    vc.stop()

    bot.queues[guild.id].clear()

    await interaction.response.send_message(
        "⏹️ Música detenida y cola vaciada."
    )


# ============================================================
# /LEAVE
# ============================================================

@bot.tree.command(
    name="leave",
    description="Sale del canal de voz."
)
async def leave(interaction):

    guild = interaction.guild

    if not guild:
        return await interaction.response.send_message(
            "❌ Solo funciona en servidores."
        )

    task = bot.queue_tasks.get(guild.id)

    if task and not task.done():
        task.cancel()

    bot.queues[guild.id].clear()

    vc = guild.voice_client

    if vc:

        try:
            vc.stop()
        except Exception:
            pass

        try:
            await vc.disconnect(force=True)
        except Exception:
            pass

    bot.join_channels.pop(
        guild.id,
        None
    )

    await interaction.response.send_message(
        "👋 Salí del canal y limpié la cola."
    )


# ============================================================
# ERRORES
# ============================================================

@bot.tree.error
async def command_error(
    interaction,
    error
):

    log.exception(
        "Error de comando: %s",
        error
    )

    try:

        message = f"❌ Error:\n`{error}`"

        if interaction.response.is_done():

            await interaction.followup.send(
                message,
                ephemeral=True
            )

        else:

            await interaction.response.send_message(
                message,
                ephemeral=True
            )

    except Exception:
        pass


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    print("🚀 Iniciando Azulita...")

    if not TOKEN:

        raise RuntimeError(
            "Falta la variable DISCORD_TOKEN en Railway."
        )

    if shutil.which("ffmpeg") is None:

        raise RuntimeError(
            "FFmpeg no está instalado/disponible en PATH."
        )

    bot.run(TOKEN)
