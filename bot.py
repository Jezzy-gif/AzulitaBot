import asyncio
import logging
import os
import shutil
import sys
from collections import defaultdict
from urllib.parse import urlparse, parse_qs

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp


# ============================================================
# CONFIGURACIÓN
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")

FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")

YOUTUBE_COOKIES = os.getenv("YOUTUBE_COOKIES", "")
COOKIES_FILE = "/tmp/youtube_cookies.txt"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("Azulita")


# ============================================================
# COOKIES
# ============================================================

if YOUTUBE_COOKIES.strip():

    try:
        with open(
            COOKIES_FILE,
            "w",
            encoding="utf-8",
            newline="\n"
        ) as f:
            f.write(YOUTUBE_COOKIES)

        os.chmod(COOKIES_FILE, 0o600)

        print("🍪 Cookies de YouTube cargadas.")

    except Exception as e:
        print(f"❌ Error guardando cookies: {e}")

else:
    print("⚠️ YOUTUBE_COOKIES no está configurada.")


# ============================================================
# COMPROBACIONES
# ============================================================

print()
print("======================================")
print("🔍 COMPROBANDO INSTALACIÓN")
print("======================================")
print(f"🐍 Python: {sys.version}")
print(f"📦 discord.py: {discord.__version__}")
print(f"📦 yt-dlp: {yt_dlp.version.__version__}")
print(f"🎧 FFmpeg: {FFMPEG_PATH}")


if shutil.which(FFMPEG_PATH) is None:

    print("❌ NO SE ENCONTRÓ FFmpeg")
    print("Instala FFmpeg o configura FFMPEG_PATH.")

    sys.exit(1)


print("✅ FFmpeg encontrado.")


if os.path.isfile(COOKIES_FILE):
    print("🍪 Cookies disponibles para yt-dlp.")
else:
    print("⚠️ yt-dlp funcionará sin cookies.")


try:

    import davey

    print(
        "🔐 davey:",
        getattr(davey, "__version__", "instalado")
    )

except ImportError:

    print("❌ davey no está instalado.")
    sys.exit(1)


print("======================================")
print()


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

        # Cola:
        # guild_id -> lista de canciones
        self.queues = defaultdict(list)

        # Locks
        self.voice_locks = defaultdict(
            asyncio.Lock
        )

        # Evita que dos canciones se reproduzcan simultáneamente
        self.play_locks = defaultdict(
            asyncio.Lock
        )

        # Canal donde debe permanecer
        self.join_channels = {}

        # Canción actual
        self.current_song = {}

    async def setup_hook(self):

        try:

            synced = await self.tree.sync()

            log.info(
                "Comandos sincronizados: %s",
                len(synced)
            )

        except Exception as e:

            log.exception(
                "Error sincronizando comandos: %s",
                e
            )

    async def on_ready(self):

        log.info("======================================")
        log.info("BOT CONECTADO: %s", self.user)
        log.info("discord.py: %s", discord.__version__)
        log.info("yt-dlp: %s", yt_dlp.version.__version__)
        log.info("FFmpeg: %s", FFMPEG_PATH)
        log.info("======================================")


bot = MusicBot()


# ============================================================
# YT-DLP
# ============================================================

BASE_YTDLP_OPTIONS = {

    "format": "bestaudio/best",

    "noplaylist": False,

    "quiet": True,

    "no_warnings": True,

    "default_search": "ytsearch",

    "source_address": "0.0.0.0",

    "extract_flat": False,

    "skip_download": True,

    "geo_bypass": True,

    "nocheckcertificate": True,

    "retries": 5,

    "fragment_retries": 5,

    "socket_timeout": 30,

    "ignoreerrors": False,

    "playlistend": 100,

}


if os.path.isfile(COOKIES_FILE):

    BASE_YTDLP_OPTIONS["cookiefile"] = COOKIES_FILE


# ============================================================
# CLIENTES
# ============================================================

YOUTUBE_CLIENTS = [
    "android_vr",
    "android",
    "web_embedded",
    "mweb",
    "tv",
]


# ============================================================
# DETECTAR PLAYLIST
# ============================================================

def is_playlist_url(url):

    try:

        parsed = urlparse(url)

        query = parse_qs(parsed.query)

        return "list" in query

    except Exception:

        return False


# ============================================================
# EXTRAER INFORMACIÓN
# ============================================================

async def extract_youtube(
    query,
    playlist=False
):

    last_error = None

    for client in YOUTUBE_CLIENTS:

        try:

            options = BASE_YTDLP_OPTIONS.copy()

            options["noplaylist"] = not playlist

            options["extractor_args"] = {

                "youtube": {

                    "player_client": [
                        client
                    ]

                }

            }

            log.info(
                "YouTube client: %s",
                client
            )

            loop = asyncio.get_running_loop()

            def extract():

                with yt_dlp.YoutubeDL(
                    options
                ) as ydl:

                    return ydl.extract_info(
                        query,
                        download=False
                    )

            data = await loop.run_in_executor(
                None,
                extract
            )

            if not data:
                continue

            return data

        except Exception as e:

            last_error = e

            log.warning(
                "Cliente %s falló: %s",
                client,
                e
            )

    if last_error:

        raise RuntimeError(
            f"{last_error}"
        )

    raise RuntimeError(
        "YouTube no devolvió información."
    )


# ============================================================
# NORMALIZAR CANCIONES
# ============================================================

def normalize_entries(data):

    songs = []

    if not data:
        return songs

    # Playlist
    if "entries" in data:

        entries = data.get("entries") or []

        for entry in entries:

            if not entry:
                continue

            url = entry.get("webpage_url")

            if not url:

                video_id = entry.get("id")

                if video_id:
                    url = (
                        "https://www.youtube.com/watch?v="
                        + video_id
                    )

            if not url:
                continue

            title = entry.get(
                "title",
                "Canción desconocida"
            )

            songs.append({
                "title": title,
                "url": url
            })

        return songs

    # Canción individual

    url = data.get("webpage_url")

    if not url:

        video_id = data.get("id")

        if video_id:
            url = (
                "https://www.youtube.com/watch?v="
                + video_id
            )

    if url:

        songs.append({
            "title": data.get(
                "title",
                "Canción desconocida"
            ),
            "url": url
        })

    return songs


# ============================================================
# OBTENER AUDIO
# ============================================================

async def get_audio_info(url):

    options = BASE_YTDLP_OPTIONS.copy()

    options["noplaylist"] = True

    last_error = None

    for client in YOUTUBE_CLIENTS:

        try:

            options["extractor_args"] = {

                "youtube": {

                    "player_client": [
                        client
                    ]

                }

            }

            loop = asyncio.get_running_loop()

            def extract():

                with yt_dlp.YoutubeDL(
                    options
                ) as ydl:

                    return ydl.extract_info(
                        url,
                        download=False
                    )

            data = await loop.run_in_executor(
                None,
                extract
            )

            if not data:
                continue

            if "entries" in data:

                entries = data.get("entries") or []

                if not entries:
                    continue

                data = entries[0]

            audio_url = data.get("url")

            if audio_url:

                return {
                    "url": audio_url,
                    "title": data.get(
                        "title",
                        "Canción desconocida"
                    ),
                    "webpage_url": data.get(
                        "webpage_url",
                        url
                    )
                }

        except Exception as e:

            last_error = e

            log.warning(
                "Audio client %s falló: %s",
                client,
                e
            )

    raise RuntimeError(
        f"No se pudo obtener el audio.\n{last_error}"
    )


# ============================================================
# CONECTAR A VOZ
# ============================================================

async def connect_to_voice(
    guild,
    channel
):

    lock = bot.voice_locks[guild.id]

    async with lock:

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
                "Conectado a voz: %s",
                channel.name
            )

            return vc

        except Exception as e:

            raise RuntimeError(
                f"No pude conectarme a voz: {e}"
            )


# ============================================================
# CREAR AUDIO
# ============================================================

def create_audio_source(audio_url):

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
# REPRODUCIR SIGUIENTE
# ============================================================

async def play_next(guild):

    lock = bot.play_locks[guild.id]

    async with lock:

        vc = guild.voice_client

        if not vc or not vc.is_connected():
            return

        if vc.is_playing() or vc.is_paused():
            return

        queue = bot.queues[guild.id]

        if not queue:

            bot.current_song.pop(
                guild.id,
                None
            )

            return

        song = queue.pop(0)

        try:

            log.info(
                "Obteniendo audio: %s",
                song["title"]
            )

            data = await get_audio_info(
                song["url"]
            )

            source = create_audio_source(
                data["url"]
            )

            bot.current_song[
                guild.id
            ] = song

            def after(error):

                if error:

                    log.error(
                        "Error de reproducción: %s",
                        error
                    )

                asyncio.run_coroutine_threadsafe(
                    play_next(guild),
                    bot.loop
                )

            vc.play(
                source,
                after=after
            )

            log.info(
                "▶️ Reproduciendo: %s",
                song["title"]
            )

        except Exception as e:

            log.error(
                "No se pudo reproducir %s: %s",
                song["title"],
                e
            )

            bot.current_song.pop(
                guild.id,
                None
            )

            await play_next(guild)


# ============================================================
# /JOIN
# ============================================================

@bot.tree.command(
    name="join",
    description="Entra a tu canal de voz."
)
async def join(interaction):

    await interaction.response.defer()

    guild = interaction.guild

    if not guild:
        await interaction.followup.send(
            "❌ Solo funciona en servidores."
        )
        return

    member = interaction.user

    if not isinstance(
        member,
        discord.Member
    ):
        return

    if not member.voice or not member.voice.channel:

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

        bot.join_channels[
            guild.id
        ] = channel.id

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
    description="Reproduce una canción o playlist."
)
@app_commands.describe(
    link="URL de YouTube o nombre de la canción"
)
async def play(
    interaction,
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

    if not isinstance(
        member,
        discord.Member
    ):
        return

    if not member.voice or not member.voice.channel:

        await interaction.followup.send(
            "❌ Entra primero a un canal de voz."
        )

        return

    channel = member.voice.channel

    try:

        vc = await connect_to_voice(
            guild,
            channel
        )

        bot.join_channels[
            guild.id
        ] = channel.id

    except Exception as e:

        await interaction.followup.send(
            f"❌ {e}"
        )

        return

    await interaction.followup.send(
        "🔎 Buscando..."
    )

    try:

        playlist = is_playlist_url(link)

        data = await extract_youtube(
            link,
            playlist=playlist
        )

        songs = normalize_entries(
            data
        )

        if not songs:

            raise RuntimeError(
                "YouTube no devolvió canciones."
            )

        # Limitar para evitar meter miles
        # de canciones accidentalmente
        songs = songs[:100]

        was_empty = (
            len(bot.queues[guild.id]) == 0
            and not vc.is_playing()
        )

        bot.queues[
            guild.id
        ].extend(songs)

        if playlist:

            await interaction.channel.send(
                f"📃 Playlist agregada: "
                f"**{len(songs)} canciones**."
            )

        else:

            await interaction.channel.send(
                f"➕ Agregada a la cola: "
                f"**{songs[0]['title']}**"
            )

        if was_empty:

            await play_next(guild)

            current = bot.current_song.get(
                guild.id
            )

            if current:

                await interaction.channel.send(
                    f"🎵 **Reproduciendo**\n"
                    f"**{current['title']}**\n"
                    f"🔊 Volumen: 70%"
                )

    except Exception as e:

        log.exception(
            "Error procesando YouTube"
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
async def queue(interaction):

    guild = interaction.guild

    if not guild:
        return

    songs = bot.queues[
        guild.id
    ]

    current = bot.current_song.get(
        guild.id
    )

    text = ""

    if current:

        text += (
            f"▶️ **Ahora:** "
            f"{current['title']}\n\n"
        )

    if not songs:

        text += "📭 La cola está vacía."

        await interaction.response.send_message(
            text
        )

        return

    for i, song in enumerate(
        songs[:20],
        1
    ):

        text += (
            f"**{i}.** "
            f"{song['title']}\n"
        )

    if len(songs) > 20:

        text += (
            f"\n... y "
            f"{len(songs) - 20} más."
        )

    await interaction.response.send_message(
        f"🎵 **Cola**\n\n{text}"
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
        return

    vc = guild.voice_client

    if not vc or not vc.is_connected():

        await interaction.response.send_message(
            "❌ No estoy en voz."
        )

        return

    if not vc.is_playing() and not vc.is_paused():

        await interaction.response.send_message(
            "❌ No hay música."
        )

        return

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

    guild = interaction.guild

    vc = (
        guild.voice_client
        if guild
        else None
    )

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
async def resume(interaction):

    guild = interaction.guild

    vc = (
        guild.voice_client
        if guild
        else None
    )

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
    description="Detiene y limpia la cola."
)
async def stop(interaction):

    guild = interaction.guild

    if not guild:
        return

    vc = guild.voice_client

    bot.queues[
        guild.id
    ].clear()

    bot.current_song.pop(
        guild.id,
        None
    )

    if vc:

        if vc.is_playing() or vc.is_paused():
            vc.stop()

    await interaction.response.send_message(
        "⏹️ Música detenida y cola limpiada.\n"
        "🔊 Sigo conectado al canal."
    )


# ============================================================
# /LEAVE
# ============================================================

@bot.tree.command(
    name="leave",
    description="Sale del canal y limpia la cola."
)
async def leave(interaction):

    guild = interaction.guild

    if not guild:
        return

    vc = guild.voice_client

    bot.queues[
        guild.id
    ].clear()

    bot.current_song.pop(
        guild.id,
        None
    )

    bot.join_channels.pop(
        guild.id,
        None
    )

    if vc:

        try:

            if vc.is_playing():
                vc.stop()

            await vc.disconnect(
                force=True
            )

        except Exception as e:

            await interaction.response.send_message(
                f"❌ {e}"
            )

            return

    await interaction.response.send_message(
        "👋 Salí del canal de voz."
    )


# ============================================================
# ERRORES
# ============================================================

@bot.tree.error
async def on_app_command_error(
    interaction,
    error
):

    log.exception(
        "Error en slash command: %s",
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
            "Falta DISCORD_TOKEN en Railway."
        )

    bot.run(TOKEN)
