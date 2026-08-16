import asyncio
import logging
import os
import shutil
import sys
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp


# ============================================================
# CONFIGURACIÓN
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")

FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")

COOKIES_FILE = "/tmp/youtube_cookies.txt"
YOUTUBE_COOKIES = os.getenv("YOUTUBE_COOKIES", "")


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

        try:
            os.chmod(COOKIES_FILE, 0o600)
        except Exception:
            pass

        print("🍪 Cookies de YouTube cargadas.")

    except Exception as e:
        print(f"❌ Error guardando cookies: {e}")

else:
    print("⚠️ No existe YOUTUBE_COOKIES.")


# ============================================================
# FFmpeg
# ============================================================

print()
print("======================================")
print("🔍 COMPROBANDO INSTALACIÓN")
print("======================================")
print(f"🐍 Python: {sys.version}")
print(f"📦 discord.py: {discord.__version__}")
print(f"📦 yt-dlp: {yt_dlp.version.__version__}")
print(f"🎧 FFmpeg: {FFMPEG_PATH}")


ffmpeg_executable = shutil.which(FFMPEG_PATH)

if not ffmpeg_executable:

    print("❌ FFmpeg NO está instalado o no está en PATH.")
    print("FFmpeg es obligatorio para reproducir música.")

    sys.exit(1)


FFMPEG_PATH = ffmpeg_executable

print(f"✅ FFmpeg encontrado: {FFMPEG_PATH}")


if os.path.isfile(COOKIES_FILE):
    print("🍪 yt-dlp utilizará las cookies.")
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

        self.queues = defaultdict(list)

        self.voice_locks = defaultdict(
            asyncio.Lock
        )

        self.join_channels = {}


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

        log.info(
            "BOT CONECTADO: %s",
            self.user
        )

        log.info(
            "Discord.py: %s",
            discord.__version__
        )

        log.info(
            "yt-dlp: %s",
            yt_dlp.version.__version__
        )

        log.info(
            "FFmpeg: %s",
            FFMPEG_PATH
        )

        log.info("======================================")


bot = MusicBot()


# ============================================================
# YT-DLP
# ============================================================

YTDLP_BASE = {

    "format": "bestaudio/best",

    "noplaylist": True,

    "quiet": True,

    "no_warnings": True,

    "default_search": "ytsearch",

    "source_address": "0.0.0.0",

    "extract_flat": False,

    "skip_download": True,

    "retries": 3,

    "fragment_retries": 3,

    "socket_timeout": 30,

    "geo_bypass": True,

    "nocheckcertificate": True,
}


if os.path.isfile(COOKIES_FILE):

    YTDLP_BASE["cookiefile"] = COOKIES_FILE


# ============================================================
# OBTENER URL DE AUDIO
# ============================================================

async def get_youtube_info(query):

    loop = asyncio.get_running_loop()

    options = YTDLP_BASE.copy()

    options["extractor_args"] = {
        "youtube": {
            "player_client": [
                "android_vr",
                "tv",
                "web_embedded",
                "mweb"
            ]
        }
    }

    def extract():

        with yt_dlp.YoutubeDL(options) as ydl:

            return ydl.extract_info(
                query,
                download=False
            )

    data = await loop.run_in_executor(
        None,
        extract
    )

    if not data:

        raise RuntimeError(
            "yt-dlp no devolvió información."
        )

    if "entries" in data:

        entries = data.get("entries")

        if not entries:

            raise RuntimeError(
                "No se encontró la canción."
            )

        data = entries[0]

    if not data.get("url"):

        raise RuntimeError(
            "YouTube no entregó una URL de audio."
        )

    return data


# ============================================================
# CONEXIÓN A VOZ
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
                timeout=60
            )

            log.info(
                "🔊 Conectado a voz: %s",
                channel.name
            )

            return vc

        except Exception as e:

            log.exception(
                "Error conectando a voz."
            )

            raise RuntimeError(
                f"No pude conectarme a voz: {e}"
            )


# ============================================================
# CREAR AUDIO
# ============================================================

def create_audio_source(audio_url):

    log.info(
        "🎧 Creando fuente FFmpeg..."
    )

    source = discord.FFmpegPCMAudio(

        audio_url,

        executable=FFMPEG_PATH,

        before_options=(
            "-reconnect 1 "
            "-reconnect_streamed 1 "
            "-reconnect_delay_max 5 "
            "-nostdin"
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
# REPRODUCIR
# ============================================================

async def play_audio(
    vc,
    link,
    title
):

    # IMPORTANTE:
    # volvemos a obtener la URL justo antes
    # de reproducir.

    log.info(
        "🔄 Extrayendo nuevamente el audio..."
    )

    data = await get_youtube_info(link)

    audio_url = data.get("url")

    if not audio_url:

        raise RuntimeError(
            "No se obtuvo una URL de audio."
        )


    source = create_audio_source(
        audio_url
    )


    finished = asyncio.Event()


    def after(error):

        if error:

            log.error(
                "❌ FFmpeg/Discord terminó con error: %s",
                error
            )

        else:

            log.info(
                "✅ Reproducción terminada."
            )

        try:

            loop = asyncio.get_running_loop()

            loop.call_soon_threadsafe(
                finished.set
            )

        except RuntimeError:

            pass


    vc.play(
        source,
        after=after
    )

    log.info(
        "🎵 Reproduciendo: %s",
        title
    )

    return source


# ============================================================
# /JOIN
# ============================================================

@bot.tree.command(
    name="join",
    description="Hace que el bot entre a tu canal de voz."
)
async def join(interaction):

    await interaction.response.defer()

    guild = interaction.guild

    if guild is None:

        await interaction.followup.send(
            "❌ Solo funciona en servidores."
        )

        return


    member = interaction.user

    if not isinstance(
        member,
        discord.Member
    ):

        await interaction.followup.send(
            "❌ No pude comprobar tu canal."
        )

        return


    if (
        not member.voice
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

        bot.join_channels[
            guild.id
        ] = channel.id


        await interaction.followup.send(
            f"🔊 Conectado a **{channel.name}**."
        )


    except Exception as e:

        await interaction.followup.send(
            f"❌ Error:\n`{e}`"
        )


# ============================================================
# /LEAVE
# ============================================================

@bot.tree.command(
    name="leave",
    description="Saca al bot del canal."
)
async def leave(interaction):

    await interaction.response.defer()

    guild = interaction.guild

    if guild is None:

        return


    vc = guild.voice_client

    if not vc:

        await interaction.followup.send(
            "❌ No estoy conectado."
        )

        return


    try:

        if vc.is_playing():

            vc.stop()


        await vc.disconnect(
            force=True
        )


        bot.join_channels.pop(
            guild.id,
            None
        )


        bot.queues[
            guild.id
        ].clear()


        await interaction.followup.send(
            "👋 Salí del canal."
        )


    except Exception as e:

        await interaction.followup.send(
            f"❌ Error:\n`{e}`"
        )


# ============================================================
# /PLAY
# ============================================================

@bot.tree.command(
    name="play",
    description="Reproduce música de YouTube."
)
@app_commands.describe(
    link="Link o búsqueda de YouTube"
)
async def play(
    interaction,
    link: str
):

    await interaction.response.defer()

    guild = interaction.guild

    if guild is None:

        await interaction.followup.send(
            "❌ Solo funciona en servidores."
        )

        return


    member = interaction.user


    if not isinstance(
        member,
        discord.Member
    ):

        await interaction.followup.send(
            "❌ No pude comprobar tu canal."
        )

        return


    if (
        not member.voice
        or not member.voice.channel
    ):

        await interaction.followup.send(
            "❌ Entra primero a un canal de voz."
        )

        return


    channel = member.voice.channel


    try:

        # ----------------------------------------------------
        # CONECTAR
        # ----------------------------------------------------

        vc = await connect_to_voice(
            guild,
            channel
        )


        bot.join_channels[
            guild.id
        ] = channel.id


        await interaction.followup.send(
            "🔎 Buscando la canción..."
        )


        # ----------------------------------------------------
        # PRIMERA EXTRACCIÓN
        # ----------------------------------------------------

        data = await get_youtube_info(
            link
        )


        title = data.get(
            "title",
            "Canción desconocida"
        )


        webpage = data.get(
            "webpage_url",
            link
        )


        await interaction.channel.send(
            f"🎵 Encontrado: **{title}**"
        )


        # ----------------------------------------------------
        # DETENER AUDIO ANTERIOR
        # ----------------------------------------------------

        if (
            vc.is_playing()
            or vc.is_paused()
        ):

            vc.stop()

            await asyncio.sleep(
                0.3
            )


        # ----------------------------------------------------
        # SEGUNDA EXTRACCIÓN
        # ----------------------------------------------------

        await interaction.channel.send(
            "🔄 Preparando el audio..."
        )


        await play_audio(
            vc,
            webpage,
            title
        )


        await interaction.channel.send(
            f"🎵 **Reproduciendo**\n"
            f"**{title}**\n"
            f"🔊 Volumen: 70%\n"
            f"🔗 {webpage}"
        )


    except Exception as e:

        log.exception(
            "❌ ERROR EN /PLAY"
        )


        try:

            await interaction.channel.send(
                "❌ No pude reproducir el audio.\n"
                f"```{e}```"
            )

        except Exception:

            pass


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


    if not vc:

        await interaction.response.send_message(
            "❌ No estoy conectado."
        )

        return


    if not vc.is_playing():

        await interaction.response.send_message(
            "❌ No hay música."
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
    description="Continúa la música."
)
async def resume(interaction):

    vc = (
        interaction.guild.voice_client
        if interaction.guild
        else None
    )


    if not vc:

        await interaction.response.send_message(
            "❌ No estoy conectado."
        )

        return


    if not vc.is_paused():

        await interaction.response.send_message(
            "❌ No está pausada."
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
    description="Detiene la música."
)
async def stop(interaction):

    vc = (
        interaction.guild.voice_client
        if interaction.guild
        else None
    )


    if not vc:

        await interaction.response.send_message(
            "❌ No estoy conectado."
        )

        return


    if (
        not vc.is_playing()
        and not vc.is_paused()
    ):

        await interaction.response.send_message(
            "❌ No hay música."
        )

        return


    vc.stop()


    await interaction.response.send_message(
        "⏹️ Música detenida.\n"
        "🔊 Sigo conectado."
    )


# ============================================================
# /SKIP
# ============================================================

@bot.tree.command(
    name="skip",
    description="Salta la canción."
)
async def skip(interaction):

    vc = (
        interaction.guild.voice_client
        if interaction.guild
        else None
    )


    if not vc:

        await interaction.response.send_message(
            "❌ No estoy conectado."
        )

        return


    if (
        not vc.is_playing()
        and not vc.is_paused()
    ):

        await interaction.response.send_message(
            "❌ No hay ninguna canción."
        )

        return


    vc.stop()


    await interaction.response.send_message(
        "⏭️ Canción saltada."
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

    if guild is None:

        await interaction.response.send_message(
            "❌ Solo funciona en servidores."
        )

        return


    songs = bot.queues[
        guild.id
    ]


    if not songs:

        await interaction.response.send_message(
            "📭 La cola está vacía."
        )

        return


    text = "\n".join(
        f"**{i + 1}.** {song}"
        for i, song in enumerate(
            songs[:20]
        )
    )


    await interaction.response.send_message(
        f"🎵 **Cola:**\n{text}"
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
            "❌ Error ejecutando el comando.\n"
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

    print(
        "🚀 Iniciando bot..."
    )


    if not TOKEN:

        raise RuntimeError(
            "Falta DISCORD_TOKEN en Railway."
        )


    bot.run(
        TOKEN
    )
