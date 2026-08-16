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
        with open(COOKIES_FILE, "w", encoding="utf-8", newline="\n") as f:
            f.write(COOKIES_TEXT)
        os.chmod(COOKIES_FILE, 0o600)
        print("🍪 Cookies de YouTube cargadas.")
    except Exception as e:
        print(f"⚠️ No se pudieron guardar las cookies: {e}")
else:
    print("⚠️ YOUTUBE_COOKIES no está configurada.")


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
        self.queues = defaultdict(list)
        self.queue_tasks = {}
        self.voice_locks = defaultdict(asyncio.Lock)
        self.join_channels = {}


bot = MusicBot()


# ============================================================
# YT-DLP
# ============================================================

YTDLP_BASE = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",
    "extract_flat": False,
    "skip_download": True,
    "retries": 5,
    "fragment_retries": 5,
    "socket_timeout": 30,
    "geo_bypass": True,
}

if os.path.isfile(COOKIES_FILE):
    YTDLP_BASE["cookiefile"] = COOKIES_FILE


async def yt_extract(query, playlist=False):
    options = YTDLP_BASE.copy()
    options["noplaylist"] = not playlist

    loop = asyncio.get_running_loop()

    def extract():
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(query, download=False)

    return await loop.run_in_executor(None, extract)


# ============================================================
# OBTENER CANCIONES / PLAYLIST
# ============================================================

async def get_tracks(query):
    data = None

    try:
        is_playlist = (
            "list=" in query.lower()
            or "playlist" in query.lower()
        )

        data = await yt_extract(
            query,
            playlist=is_playlist
        )

        if not data:
            raise RuntimeError("YouTube no devolvió información.")

        # Playlist
        if is_playlist and "entries" in data:
            tracks = []

            for entry in data.get("entries") or []:
                if not entry:
                    continue

                webpage = entry.get("webpage_url")

                if not webpage:
                    webpage = entry.get("original_url")

                if not webpage:
                    video_id = entry.get("id")
                    if video_id:
                        webpage = f"https://www.youtube.com/watch?v={video_id}"

                if not webpage:
                    continue

                tracks.append({
                    "title": entry.get("title", "Canción desconocida"),
                    "url": webpage
                })

            if not tracks:
                raise RuntimeError(
                    "La playlist no contiene canciones válidas."
                )

            return tracks

        # Búsqueda / vídeo individual
        if "entries" in data:
            entries = [x for x in (data.get("entries") or []) if x]

            if not entries:
                raise RuntimeError("No se encontró la canción.")

            data = entries[0]

        webpage = data.get("webpage_url")

        if not webpage:
            video_id = data.get("id")
            if video_id:
                webpage = f"https://www.youtube.com/watch?v={video_id}"

        if not webpage:
            webpage = query

        return [{
            "title": data.get("title", "Canción desconocida"),
            "url": webpage
        }]

    except Exception as e:
        log.exception("Error obteniendo canciones")
        raise RuntimeError(
            f"No se pudo obtener la canción o playlist.\n\n{e}"
        )


# ============================================================
# OBTENER URL DE AUDIO + HEADERS
# ============================================================

async def get_audio_url(webpage):
    try:
        data = await yt_extract(webpage, playlist=False)

        if not data:
            raise RuntimeError("YouTube no devolvió información.")

        if "entries" in data:
            entries = [x for x in (data.get("entries") or []) if x]

            if not entries:
                raise RuntimeError("No se encontró el audio.")

            data = entries[0]

        audio_url = data.get("url")

        if not audio_url:
            raise RuntimeError(
                "YouTube no entregó una URL de audio."
            )

        return {
            "audio_url": audio_url,
            "title": data.get("title", "Canción desconocida"),
            "webpage": data.get("webpage_url", webpage),
            "headers": data.get("http_headers", {}) or {}
        }

    except Exception as e:
        log.exception("Error extrayendo audio")
        raise RuntimeError(str(e))


# ============================================================
# VOZ
# ============================================================

async def connect_voice(guild, channel):
    async with bot.voice_locks[guild.id]:
        vc = guild.voice_client

        if vc and vc.is_connected():
            if vc.channel and vc.channel.id == channel.id:
                bot.join_channels[guild.id] = channel.id
                return vc

            try:
                await vc.move_to(channel)
                bot.join_channels[guild.id] = channel.id
                return vc
            except Exception as e:
                log.warning("No se pudo mover el bot: %s", e)

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
            log.info("Conectado a voz: %s", channel.name)
            return vc

        except Exception as e:
            raise RuntimeError(
                f"No pude conectarme al canal: {e}"
            )


# ============================================================
# FFMPEG
# ============================================================

def create_source(audio_url, headers=None):
    headers = headers or {}

    header_lines = []

    for key, value in headers.items():
        if key.lower() in {"range", "content-length"}:
            continue
        header_lines.append(f"{key}: {value}")

    before_options = (
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5 "
        "-reconnect_on_network_error 1 "
        "-reconnect_at_eof 1 "
    )

    if header_lines:
        # FFmpeg acepta headers separados por CRLF.
        header_value = "\\r\\n".join(header_lines) + "\\r\\n"
        before_options += f'-headers "{header_value}" '

    return discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(
            audio_url,
            executable=FFMPEG_PATH,
            before_options=before_options,
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
# REPRODUCTOR DE COLA
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
                    log.warning("No hay canal guardado para %s", guild.name)
                    break

                channel = guild.get_channel(channel_id)

                if not channel:
                    log.warning("No se encontró el canal guardado.")
                    break

                vc = await connect_voice(guild, channel)

            # ------------------------------------------------
            # EXTRAER AUDIO FRESCO
            # ------------------------------------------------

            try:
                log.info("Extrayendo: %s", item["title"])

                info = await get_audio_url(item["url"])

                audio_url = info["audio_url"]
                title = info["title"]
                headers = info.get("headers", {})

                item["title"] = title

            except Exception as e:
                log.error(
                    "No se pudo reproducir %s: %s",
                    item["title"],
                    e
                )

                bot.queues[guild_id].pop(0)
                continue

            # ------------------------------------------------
            # EVENTO DE FIN
            # ------------------------------------------------

            finished = asyncio.Event()
            playback_error = None

            # IMPORTANTE:
            # after() corre desde el hilo de audio de discord.py.
            # Guardamos el loop ANTES de iniciar FFmpeg.
            loop = asyncio.get_running_loop()

            def after(error):
                nonlocal playback_error

                playback_error = error

                try:
                    loop.call_soon_threadsafe(
                        finished.set
                    )
                except Exception:
                    pass

            # ------------------------------------------------
            # INICIAR AUDIO
            # ------------------------------------------------

            try:
                if vc.is_playing() or vc.is_paused():
                    vc.stop()
                    await asyncio.sleep(0.2)

                source = create_source(
                    audio_url,
                    headers
                )

                vc.play(
                    source,
                    after=after
                )

            except Exception as e:
                log.exception("Error iniciando FFmpeg")

                try:
                    source.cleanup()
                except Exception:
                    pass

                bot.queues[guild_id].pop(0)
                continue

            log.info("▶️ Reproduciendo: %s", title)

            # ------------------------------------------------
            # ESPERAR FIN
            # ------------------------------------------------

            try:
                await asyncio.wait_for(
                    finished.wait(),
                    timeout=60 * 60 * 4
                )
            except asyncio.TimeoutError:
                log.error(
                    "La canción tardó demasiado. Se salta: %s",
                    title
                )

                try:
                    if vc.is_playing():
                        vc.stop()
                except Exception:
                    pass

            if playback_error:
                log.error(
                    "Error reproduciendo %s: %s",
                    title,
                    playback_error
                )

            # ------------------------------------------------
            # QUITAR DE LA COLA
            # ------------------------------------------------

            if bot.queues[guild_id]:
                bot.queues[guild_id].pop(0)

        log.info("Cola terminada en %s", guild.name)

    except asyncio.CancelledError:
        log.info("Cola cancelada en %s", guild.name)
        raise

    except Exception:
        log.exception("Error en reproductor de cola")

    finally:
        bot.queue_tasks.pop(guild_id, None)


def start_queue(guild):
    task = bot.queue_tasks.get(guild.id)

    if task and not task.done():
        return

    bot.queue_tasks[guild.id] = asyncio.create_task(
        play_queue(guild)
    )


# ============================================================
# READY
# ============================================================

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()

        log.info("======================================")
        log.info("BOT CONECTADO: %s", bot.user)
        log.info("Comandos sincronizados: %s", len(synced))
        log.info("yt-dlp: %s", yt_dlp.version.__version__)
        log.info("FFmpeg: %s", FFMPEG_PATH)
        log.info(
            "Cookies: %s",
            "SI" if os.path.isfile(COOKIES_FILE) else "NO"
        )
        log.info("======================================")

    except Exception:
        log.exception("Error en on_ready")


# ============================================================
# /JOIN
# ============================================================

@bot.tree.command(
    name="join",
    description="Entra a tu canal de voz."
)
async def join(interaction: discord.Interaction):
    await interaction.response.defer()

    if not interaction.guild:
        return await interaction.followup.send(
            "❌ Este comando solo funciona en servidores."
        )

    member = interaction.user

    if not isinstance(member, discord.Member):
        return await interaction.followup.send(
            "❌ No pude comprobar tu canal."
        )

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
async def play(
    interaction: discord.Interaction,
    query: str
):
    await interaction.response.defer()

    guild = interaction.guild

    if not guild:
        return await interaction.followup.send(
            "❌ Solo funciona en servidores."
        )

    member = interaction.user

    if not isinstance(member, discord.Member):
        return await interaction.followup.send(
            "❌ No pude comprobar tu canal."
        )

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

        if was_empty:
            start_queue(guild)

    except Exception as e:
        log.exception("Error en /play")

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
async def add(
    interaction: discord.Interaction,
    query: str
):
    await interaction.response.defer()

    guild = interaction.guild

    if not guild:
        return await interaction.followup.send(
            "❌ Solo funciona en servidores."
        )

    member = interaction.user

    if not isinstance(member, discord.Member):
        return await interaction.followup.send(
            "❌ No pude comprobar tu canal."
        )

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

        was_empty = not bot.queues[guild.id]

        bot.queues[guild.id].extend(tracks)

        await interaction.channel.send(
            f"✅ Se agregaron **{len(tracks)}** "
            f"canciones a la cola."
        )

        if was_empty:
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
async def queue(interaction: discord.Interaction):
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
            f"\n\n... y **{len(songs) - 20}** más."
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
async def skip(interaction: discord.Interaction):
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
async def pause(interaction: discord.Interaction):
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
async def resume(interaction: discord.Interaction):
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
    description="Detiene la música y vacía la cola."
)
async def stop(interaction: discord.Interaction):
    guild = interaction.guild

    if not guild:
        return await interaction.response.send_message(
            "❌ Solo funciona en servidores."
        )

    vc = guild.voice_client

    if vc:
        try:
            vc.stop()
        except Exception:
            pass

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
async def leave(interaction: discord.Interaction):
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

    bot.join_channels.pop(guild.id, None)

    await interaction.response.send_message(
        "👋 Salí del canal y limpié la cola."
    )


# ============================================================
# ERROR HANDLER
# ============================================================

@bot.tree.error
async def command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    log.exception("Error de comando: %s", error)

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
