import asyncio
import logging
import os
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp


# ============================================================
# CONFIGURACIÓN
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")
FFMPEG_PATH = "ffmpeg"

YOUTUBE_COOKIES = os.getenv("YOUTUBE_COOKIES", "")
COOKIES_FILE = "/tmp/youtube_cookies.txt"

if YOUTUBE_COOKIES.strip():
    try:
        with open(COOKIES_FILE, "w", encoding="utf-8", newline="\n") as f:
            f.write(YOUTUBE_COOKIES)

        try:
            os.chmod(COOKIES_FILE, 0o600)
        except Exception:
            pass

        print("🍪 Cookies de YouTube cargadas.")
    except Exception as e:
        print(f"⚠️ No se pudieron guardar las cookies: {e}")
else:
    print("⚠️ YOUTUBE_COOKIES no configurada.")


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("Azulita")


# ============================================================
# COMPROBACIONES
# ============================================================

print("======================================")
print("🔍 COMPROBANDO INSTALACIÓN")
print("======================================")
print(f"🐍 Python: {sys.version}")
print(f"📦 discord.py: {discord.__version__}")
print(f"📦 yt-dlp: {yt_dlp.version.__version__}")
print(f"🎧 FFmpeg: {FFMPEG_PATH}")

if shutil.which(FFMPEG_PATH) is None:
    print("❌ FFmpeg no está instalado o no está en PATH.")
    sys.exit(1)

print("✅ FFmpeg encontrado.")

try:
    import davey
    print("🔐 davey: instalado")
except ImportError:
    print("❌ Falta davey.")
    sys.exit(1)

print("======================================")


# ============================================================
# INTENTS
# ============================================================

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True


# ============================================================
# ESTRUCTURA DE COLA
# ============================================================

@dataclass
class Song:
    title: str
    webpage_url: str
    duration: int | None = None


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
        self.voice_locks = defaultdict(asyncio.Lock)
        self.player_locks = defaultdict(asyncio.Lock)

        # Canción que está sonando actualmente
        self.current = {}

        # Canal de voz donde debe permanecer
        self.join_channels = {}

        # Tareas de reproducción
        self.player_tasks = {}


    async def setup_hook(self):
        try:
            synced = await self.tree.sync()
            log.info("Comandos sincronizados: %s", len(synced))
        except Exception:
            log.exception("Error sincronizando comandos")


    async def on_ready(self):
        log.info("======================================")
        log.info("BOT CONECTADO: %s", self.user)
        log.info("discord.py: %s", discord.__version__)
        log.info("yt-dlp: %s", yt_dlp.version.__version__)
        log.info("======================================")


bot = MusicBot()


# ============================================================
# YT-DLP
# ============================================================

BASE_YTDLP_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",
    "extract_flat": False,
    "geo_bypass": True,
    "nocheckcertificate": True,
    "retries": 3,
    "fragment_retries": 3,
    "socket_timeout": 20,

    # IMPORTANTE:
    # Permitimos playlists para /add y /play.
    "noplaylist": False,
}

if os.path.isfile(COOKIES_FILE):
    BASE_YTDLP_OPTIONS["cookiefile"] = COOKIES_FILE


# ============================================================
# OBTENER INFORMACIÓN
# ============================================================

async def extract_info(query: str, playlist: bool = True):

    loop = asyncio.get_running_loop()

    options = BASE_YTDLP_OPTIONS.copy()
    options["noplaylist"] = not playlist

    def extract():
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(
                query,
                download=False
            )

    return await asyncio.wait_for(
        loop.run_in_executor(None, extract),
        timeout=90
    )


async def get_songs(query: str):
    """
    Devuelve una lista de Song.
    Soporta:
    - vídeos de YouTube
    - búsquedas
    - playlists
    """

    data = await extract_info(query, playlist=True)

    if not data:
        raise RuntimeError("YouTube no devolvió información.")

    songs = []

    # Playlist / búsqueda
    if "entries" in data:

        entries = data.get("entries") or []

        for entry in entries:

            if not entry:
                continue

            title = entry.get("title")

            webpage_url = (
                entry.get("webpage_url")
                or entry.get("original_url")
                or entry.get("url")
            )

            if not title or not webpage_url:
                continue

            songs.append(
                Song(
                    title=title,
                    webpage_url=webpage_url,
                    duration=entry.get("duration")
                )
            )

    else:

        title = data.get(
            "title",
            "Canción desconocida"
        )

        webpage_url = (
            data.get("webpage_url")
            or query
        )

        songs.append(
            Song(
                title=title,
                webpage_url=webpage_url,
                duration=data.get("duration")
            )
        )

    if not songs:
        raise RuntimeError(
            "No se encontró ninguna canción."
        )

    return songs


# ============================================================
# OBTENER URL DE AUDIO DE UNA CANCIÓN
# ============================================================

async def get_audio_url(song: Song):

    loop = asyncio.get_running_loop()

    options = BASE_YTDLP_OPTIONS.copy()

    options["noplaylist"] = True
    options["format"] = "bestaudio/best"

    def extract():

        with yt_dlp.YoutubeDL(options) as ydl:

            data = ydl.extract_info(
                song.webpage_url,
                download=False
            )

            if not data:
                raise RuntimeError(
                    "YouTube no devolvió información."
                )

            if "entries" in data:

                entries = data.get("entries") or []

                if not entries:
                    raise RuntimeError(
                        "No se encontró el vídeo."
                    )

                data = entries[0]

            audio_url = data.get("url")

            if not audio_url:
                raise RuntimeError(
                    "YouTube no entregó una URL de audio."
                )

            return audio_url

    return await asyncio.wait_for(
        loop.run_in_executor(None, extract),
        timeout=90
    )


# ============================================================
# CONECTAR A VOZ
# ============================================================

async def connect_to_voice(
    guild: discord.Guild,
    channel: discord.VoiceChannel
):

    async with bot.voice_locks[guild.id]:

        vc = guild.voice_client

        if vc and vc.is_connected():

            if (
                vc.channel
                and vc.channel.id == channel.id
            ):
                return vc

            try:
                await vc.move_to(channel)
                return vc

            except Exception:
                log.exception(
                    "Error moviendo el bot."
                )

        if vc:

            try:
                await vc.disconnect(
                    force=True
                )
            except Exception:
                pass

            await asyncio.sleep(1)

        try:

            vc = await channel.connect(
                reconnect=True,
                timeout=30
            )

            log.info(
                "🔊 Conectado a: %s",
                channel.name
            )

            return vc

        except Exception as e:

            raise RuntimeError(
                f"No pude conectarme a voz: {e}"
            )


# ============================================================
# FFmpeg
# ============================================================

def create_audio_source(audio_url: str):

    source = discord.FFmpegPCMAudio(
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
    )

    return discord.PCMVolumeTransformer(
        source,
        volume=0.7
    )


# ============================================================
# REPRODUCTOR DE COLA
# ============================================================

async def player_loop(guild: discord.Guild):

    guild_id = guild.id

    try:

        while True:

            # ------------------------------------------------
            # Si no hay canciones
            # ------------------------------------------------

            if not bot.queues[guild_id]:

                bot.current.pop(
                    guild_id,
                    None
                )

                return

            # ------------------------------------------------
            # Conexión
            # ------------------------------------------------

            channel_id = bot.join_channels.get(
                guild_id
            )

            if not channel_id:

                log.warning(
                    "No hay canal de voz configurado."
                )

                return

            channel = guild.get_channel(
                channel_id
            )

            if not isinstance(
                channel,
                discord.VoiceChannel
            ):

                log.warning(
                    "El canal de voz ya no existe."
                )

                return

            try:

                vc = await connect_to_voice(
                    guild,
                    channel
                )

            except Exception:

                log.exception(
                    "No se pudo conectar a voz."
                )

                await asyncio.sleep(5)
                continue

            # ------------------------------------------------
            # Sacar siguiente canción
            # ------------------------------------------------

            song = bot.queues[guild_id].pop(
                0
            )

            bot.current[guild_id] = song

            log.info(
                "🎵 Preparando: %s",
                song.title
            )

            # ------------------------------------------------
            # Obtener URL nueva
            # ------------------------------------------------

            try:

                audio_url = await get_audio_url(
                    song
                )

            except Exception as e:

                log.error(
                    "❌ No se pudo obtener '%s': %s",
                    song.title,
                    e
                )

                bot.current.pop(
                    guild_id,
                    None
                )

                continue

            # ------------------------------------------------
            # Crear audio
            # ------------------------------------------------

            try:

                source = create_audio_source(
                    audio_url
                )

            except Exception as e:

                log.error(
                    "Error creando FFmpeg: %s",
                    e
                )

                bot.current.pop(
                    guild_id,
                    None
                )

                continue

            finished = asyncio.Event()

            def after_play(error):

                if error:
                    log.error(
                        "Error reproduciendo '%s': %s",
                        song.title,
                        error
                    )

                else:
                    log.info(
                        "✅ Terminó: %s",
                        song.title
                    )

                bot_loop = bot.loop

                if bot_loop:

                    bot_loop.call_soon_threadsafe(
                        finished.set
                    )

            # ------------------------------------------------
            # Reproducir
            # ------------------------------------------------

            try:

                if vc.is_playing():
                    vc.stop()

                vc.play(
                    source,
                    after=after_play
                )

            except Exception as e:

                log.error(
                    "Error iniciando audio: %s",
                    e
                )

                bot.current.pop(
                    guild_id,
                    None
                )

                continue

            # ------------------------------------------------
            # Esperar a que termine
            # ------------------------------------------------

            try:

                await asyncio.wait_for(
                    finished.wait(),
                    timeout=7200
                )

            except asyncio.TimeoutError:

                log.warning(
                    "La canción tardó demasiado."
                )

                try:
                    vc.stop()
                except Exception:
                    pass

            bot.current.pop(
                guild_id,
                None
            )

            await asyncio.sleep(0.5)

    except asyncio.CancelledError:

        log.info(
            "Player detenido para guild %s",
            guild_id
        )

        raise

    except Exception:

        log.exception(
            "Error fatal en player_loop."
        )

    finally:

        bot.player_tasks.pop(
            guild_id,
            None
        )


# ============================================================
# INICIAR REPRODUCTOR
# ============================================================

def start_player(guild: discord.Guild):

    task = bot.player_tasks.get(
        guild.id
    )

    if task and not task.done():
        return

    bot.player_tasks[guild.id] = asyncio.create_task(
        player_loop(guild)
    )


# ============================================================
# /JOIN
# ============================================================

@bot.tree.command(
    name="join",
    description="Entra a tu canal de voz."
)
async def join(
    interaction: discord.Interaction
):

    await interaction.response.defer()

    guild = interaction.guild

    if not guild:
        await interaction.followup.send(
            "❌ Solo funciona en servidores."
        )
        return

    member = interaction.user

    if (
        not isinstance(member, discord.Member)
        or not member.voice
        or not member.voice.channel
    ):

        await interaction.followup.send(
            "❌ Primero entra a un canal de voz."
        )
        return

    channel = member.voice.channel

    try:

        await connect_to_voice(
            guild,
            channel
        )

        bot.join_channels[guild.id] = channel.id

        await interaction.followup.send(
            f"🔊 Conectado a **{channel.name}**."
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
    description="Reproduce o agrega una canción/playlist."
)
@app_commands.describe(
    link="URL de YouTube o búsqueda"
)
async def play(
    interaction: discord.Interaction,
    link: str
):

    await interaction.response.defer()

    guild = interaction.guild

    if not guild:
        await interaction.followup.send(
            "❌ Solo funciona en servidores."
        )
        return

    member = interaction.user

    if (
        not isinstance(member, discord.Member)
        or not member.voice
        or not member.voice.channel
    ):

        await interaction.followup.send(
            "❌ Entra primero a un canal de voz."
        )
        return

    channel = member.voice.channel

    try:

        await connect_to_voice(
            guild,
            channel
        )

        bot.join_channels[guild.id] = channel.id

        await interaction.followup.send(
            "🔎 Buscando..."
        )

        songs = await get_songs(
            link
        )

        was_empty = not bot.queues[guild.id] and (
            guild.id not in bot.current
        )

        bot.queues[guild.id].extend(
            songs
        )

        if len(songs) == 1:

            msg = (
                f"🎵 **Agregado:** "
                f"**{songs[0].title}**"
            )

        else:

            msg = (
                f"📀 **Playlist agregada:** "
                f"**{len(songs)} canciones**"
            )

        await interaction.channel.send(
            msg
        )

        start_player(guild)

    except Exception as e:

        log.exception(
            "Error en /play"
        )

        await interaction.channel.send(
            "❌ **Error:**\n"
            f"`{e}`"
        )


# ============================================================
# /ADD
# ============================================================

@bot.tree.command(
    name="add",
    description="Agrega una canción o playlist a la cola."
)
@app_commands.describe(
    link="URL de YouTube o búsqueda"
)
async def add(
    interaction: discord.Interaction,
    link: str
):

    await interaction.response.defer()

    guild = interaction.guild

    if not guild:
        await interaction.followup.send(
            "❌ Solo funciona en servidores."
        )
        return

    member = interaction.user

    if (
        not isinstance(member, discord.Member)
        or not member.voice
        or not member.voice.channel
    ):

        await interaction.followup.send(
            "❌ Entra primero a un canal de voz."
        )
        return

    channel = member.voice.channel

    try:

        await connect_to_voice(
            guild,
            channel
        )

        bot.join_channels[guild.id] = channel.id

        await interaction.followup.send(
            "📥 Agregando a la cola..."
        )

        songs = await get_songs(
            link
        )

        bot.queues[guild.id].extend(
            songs
        )

        if len(songs) == 1:

            await interaction.channel.send(
                f"➕ **Agregado a la cola:**\n"
                f"🎵 {songs[0].title}\n"
                f"📍 Posición: "
                f"`{len(bot.queues[guild.id])}`"
            )

        else:

            await interaction.channel.send(
                f"📀 **Playlist agregada a la cola**\n"
                f"🎵 Canciones: `{len(songs)}`\n"
                f"📍 Posición final: "
                f"`{len(bot.queues[guild.id])}`"
            )

        start_player(guild)

    except Exception as e:

        log.exception(
            "Error en /add"
        )

        await interaction.channel.send(
            "❌ **Error:**\n"
            f"`{e}`"
        )


# ============================================================
# /QUEUE
# ============================================================

@bot.tree.command(
    name="queue",
    description="Muestra la cola."
)
async def queue(
    interaction: discord.Interaction
):

    guild = interaction.guild

    if not guild:
        await interaction.response.send_message(
            "❌ Solo funciona en servidores."
        )
        return

    current = bot.current.get(
        guild.id
    )

    songs = bot.queues[guild.id]

    text = "🎵 **MÚSICA ACTUAL**\n"

    if current:
        text += f"▶️ {current.title}\n"
    else:
        text += "Nada reproduciéndose.\n"

    text += "\n📋 **COLA**\n"

    if not songs:

        text += "La cola está vacía."

    else:

        for i, song in enumerate(
            songs[:20],
            start=1
        ):

            text += (
                f"`{i}.` {song.title}\n"
            )

        if len(songs) > 20:

            text += (
                f"\n... y "
                f"{len(songs) - 20} más."
            )

    await interaction.response.send_message(
        text
    )


# ============================================================
# /SKIP
# ============================================================

@bot.tree.command(
    name="skip",
    description="Salta la canción actual."
)
async def skip(
    interaction: discord.Interaction
):

    guild = interaction.guild

    if not guild:
        await interaction.response.send_message(
            "❌ Solo funciona en servidores."
        )
        return

    vc = guild.voice_client

    if not vc or not vc.is_playing():

        await interaction.response.send_message(
            "❌ No hay ninguna canción reproduciéndose."
        )
        return

    vc.stop()

    await interaction.response.send_message(
        "⏭️ Canción saltada. "
        "Reproduciendo la siguiente..."
    )


# ============================================================
# /PAUSE
# ============================================================

@bot.tree.command(
    name="pause",
    description="Pausa la música."
)
async def pause(
    interaction: discord.Interaction
):

    guild = interaction.guild
    vc = guild.voice_client if guild else None

    if not vc or not vc.is_playing():

        await interaction.response.send_message(
            "❌ No hay música reproduciéndose."
        )
        return

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
async def resume(
    interaction: discord.Interaction
):

    guild = interaction.guild
    vc = guild.voice_client if guild else None

    if not vc or not vc.is_paused():

        await interaction.response.send_message(
            "❌ La música no está pausada."
        )
        return

    vc.resume()

    await interaction.response.send_message(
        "▶️ Música reanudada."
    )


# ============================================================
# /STOP
# ============================================================

@bot.tree.command(
    name="stop",
    description="Detiene la música y limpia la cola."
)
async def stop(
    interaction: discord.Interaction
):

    guild = interaction.guild

    if not guild:
        await interaction.response.send_message(
            "❌ Solo funciona en servidores."
        )
        return

    vc = guild.voice_client

    bot.queues[guild.id].clear()

    if vc and (
        vc.is_playing()
        or vc.is_paused()
    ):

        vc.stop()

    await interaction.response.send_message(
        "⏹️ Música detenida y cola vaciada."
    )


# ============================================================
# /LEAVE
# ============================================================

@bot.tree.command(
    name="leave",
    description="Saca al bot del canal y limpia la cola."
)
async def leave(
    interaction: discord.Interaction
):

    guild = interaction.guild

    if not guild:
        await interaction.response.send_message(
            "❌ Solo funciona en servidores."
        )
        return

    bot.queues[guild.id].clear()

    task = bot.player_tasks.get(
        guild.id
    )

    if task and not task.done():

        task.cancel()

    bot.player_tasks.pop(
        guild.id,
        None
    )

    bot.current.pop(
        guild.id,
        None
    )

    bot.join_channels.pop(
        guild.id,
        None
    )

    vc = guild.voice_client

    if vc:

        try:

            if vc.is_playing():
                vc.stop()

        except Exception:
            pass

        try:
            await vc.disconnect(
                force=True
            )
        except Exception:
            pass

    await interaction.response.send_message(
        "👋 Salí del canal y limpié la cola."
    )


# ============================================================
# ERRORES
# ============================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    log.exception(
        "Error en comando: %s",
        error
    )

    try:

        message = (
            "❌ Ocurrió un error:\n"
            f"`{error}`"
        )

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
# MAIN
# ============================================================

if __name__ == "__main__":

    print("🚀 Iniciando bot...")

    if not TOKEN:

        raise RuntimeError(
            "❌ Falta DISCORD_TOKEN en Railway."
        )

    try:

        bot.run(TOKEN)

    except KeyboardInterrupt:

        print("🛑 Bot detenido.")

    except Exception:

        log.exception(
            "El bot terminó con error."
        )
