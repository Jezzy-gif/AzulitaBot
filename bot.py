import asyncio
import logging
import os
import shutil
import subprocess
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

FFMPEG_PATH = "ffmpeg"

YOUTUBE_COOKIES = os.getenv(
    "YOUTUBE_COOKIES",
    ""
)

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
# COOKIES DE YOUTUBE
# ============================================================

if YOUTUBE_COOKIES.strip():

    try:

        with open(
            COOKIES_FILE,
            "w",
            encoding="utf-8",
            newline="\n"
        ) as f:

            f.write(
                YOUTUBE_COOKIES
            )

        os.chmod(
            COOKIES_FILE,
            0o600
        )

        print(
            "🍪 Cookies de YouTube cargadas."
        )

    except Exception as e:

        print(
            f"❌ Error guardando cookies: {e}"
        )

else:

    print(
        "⚠️ YOUTUBE_COOKIES no está configurada."
    )


# ============================================================
# COMPROBACIONES
# ============================================================

print()
print("======================================")
print("🔍 COMPROBANDO INSTALACIÓN")
print("======================================")

print(
    f"🐍 Python: {sys.version}"
)

print(
    f"📦 discord.py: {discord.__version__}"
)

print(
    f"📦 yt-dlp: {yt_dlp.version.__version__}"
)

print(
    f"🎧 FFmpeg: {FFMPEG_PATH}"
)


if shutil.which(
    FFMPEG_PATH
) is None:

    print(
        "❌ NO SE ENCONTRÓ FFmpeg"
    )

    sys.exit(1)


print(
    "✅ FFmpeg encontrado."
)


if os.path.isfile(
    COOKIES_FILE
):

    print(
        "🍪 yt-dlp utilizará las cookies."
    )

else:

    print(
        "⚠️ yt-dlp funcionará sin cookies."
    )


try:

    import davey

    print(
        "🔐 davey: "
        f"{getattr(davey, '__version__', 'instalado')}"
    )

except ImportError:

    print(
        "❌ davey no está instalado."
    )

    sys.exit(1)


# --- NUEVO: chequeo de PyNaCl (necesario para transmitir voz) ---
try:

    import nacl  # noqa: F401

    print(
        "🔑 PyNaCl: instalado."
    )

except ImportError:

    print(
        "❌ PyNaCl no está instalado. "
        "Instalá 'PyNaCl' (pip install PyNaCl) "
        "o el bot se conectará a voz pero NO podrá "
        "enviar audio."
    )

    sys.exit(1)


print(
    "======================================"
)
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

        log.info(
            "======================================"
        )

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

        log.info(
            "======================================"
        )


bot = MusicBot()


# ============================================================
# YT-DLP
# ============================================================

BASE_YTDLP_OPTIONS = {

    "format": "bestaudio/best",

    "noplaylist": True,

    "quiet": True,

    "no_warnings": True,

    "default_search": "ytsearch",

    "source_address": "0.0.0.0",

    "extract_flat": False,

    "skip_download": True,

    "geo_bypass": True,

    "nocheckcertificate": True,

    "retries": 3,

    "fragment_retries": 3,

    "socket_timeout": 20,

}


# ============================================================
# USAR COOKIES
# ============================================================

if os.path.isfile(
    COOKIES_FILE
):

    BASE_YTDLP_OPTIONS[
        "cookiefile"
    ] = COOKIES_FILE


# ============================================================
# CLIENTES YOUTUBE
# ============================================================

YOUTUBE_CLIENTS = [

    "android_vr",

    "tv",

    "web_embedded",

    "mweb",

    "web_safari",

]


# ============================================================
# EXTRAER YOUTUBE
# ============================================================

async def extract_with_client(
    query: str,
    client: str
):

    loop = asyncio.get_running_loop()

    options = (
        BASE_YTDLP_OPTIONS.copy()
    )

    options[
        "extractor_args"
    ] = {

        "youtube": {

            "player_client": [
                client
            ]

        }

    }


    def extract():

        with yt_dlp.YoutubeDL(
            options
        ) as ydl:

            return ydl.extract_info(
                query,
                download=False
            )


    return await loop.run_in_executor(
        None,
        extract
    )


async def get_youtube_info(
    query: str
):

    last_error = None


    # ========================================================
    # PROBAR CLIENTES
    # ========================================================

    for client in YOUTUBE_CLIENTS:

        try:

            log.info(
                "Probando YouTube client: %s",
                client
            )

            data = (
                await extract_with_client(
                    query,
                    client
                )
            )


            if not data:
                continue


            if "entries" in data:

                entries = data.get(
                    "entries"
                )

                if not entries:
                    continue

                data = entries[0]


            if data.get("url"):

                log.info(
                    "✅ YouTube funcionó con: %s",
                    client
                )

                return data


        except Exception as e:

            last_error = e

            log.warning(
                "Cliente %s falló: %s",
                client,
                e
            )


    # ========================================================
    # ÚLTIMO INTENTO AUTOMÁTICO
    # ========================================================

    try:

        log.info(
            "Probando configuración automática de yt-dlp..."
        )


        options = (
            BASE_YTDLP_OPTIONS.copy()
        )


        def extract_default():

            with yt_dlp.YoutubeDL(
                options
            ) as ydl:

                return ydl.extract_info(
                    query,
                    download=False
                )


        data = await asyncio.get_running_loop().run_in_executor(
            None,
            extract_default
        )


        if data:

            if "entries" in data:

                entries = data.get(
                    "entries"
                )

                if entries:

                    data = entries[0]


            if data.get("url"):

                return data


    except Exception as e:

        last_error = e


    # ========================================================
    # ERROR
    # ========================================================

    if last_error:

        raise RuntimeError(
            "YouTube bloqueó la extracción desde "
            "el servidor.\n\n"
            f"Último error: {last_error}"
        )


    raise RuntimeError(
        "No se pudo obtener el audio."
    )


# ============================================================
# CONECTAR A VOZ
# ============================================================

async def connect_to_voice(
    guild: discord.Guild,
    channel: discord.VoiceChannel
):

    lock = bot.voice_locks[
        guild.id
    ]

    async with lock:

        vc = guild.voice_client


        if vc and vc.is_connected():

            if (
                vc.channel
                and vc.channel.id == channel.id
            ):

                return vc


            try:

                await vc.move_to(
                    channel
                )

                return vc

            except Exception:

                log.exception(
                    "Error moviendo el bot."
                )

                return vc


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
                timeout=30,
                self_deaf=True,
            )

            log.info(
                "Conectado a voz: %s",
                channel.name
            )

            return vc


        except asyncio.TimeoutError:

            raise RuntimeError(
                "Discord tardó demasiado "
                "en conectar al canal."
            )


        except Exception as e:

            log.exception(
                "Error conectando a voz."
            )

            raise RuntimeError(
                f"No pude conectarme a voz: {e}"
            )


# ============================================================
# AUDIO
# ============================================================

def create_audio_source(
    audio_url: str,
    http_headers: dict | None = None,
):
    """
    IMPORTANTE: la URL directa que entrega yt-dlp (googlevideo.com)
    solo funciona si ffmpeg manda las MISMAS cabeceras HTTP
    (User-Agent, etc.) que usó yt-dlp para extraerla. Sin esto,
    YouTube devuelve 403 / stream vacío y el bot "reproduce"
    pero no suena nada.
    """

    headers = http_headers or {}

    # yt-dlp entrega headers tipo {"User-Agent": "...", "Referer": "..."}
    header_lines = "".join(
        f"{key}: {value}\r\n"
        for key, value in headers.items()
    )

    before_options = (
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5 "
    )

    if header_lines:

        # Se pasa como un solo argumento con las cabeceras separadas por \r\n
        before_options += f'-headers "{header_lines}" '

    source = discord.FFmpegPCMAudio(

        audio_url,

        executable=FFMPEG_PATH,

        before_options=before_options,

        options=(
            "-vn "
            "-loglevel warning "
            "-ac 2 "
            "-ar 48000"
        ),

        # NUEVO: capturamos stderr de ffmpeg para poder loguear
        # el motivo real si el audio falla en silencio.
        stderr=subprocess.PIPE,
    )


    return discord.PCMVolumeTransformer(
        source,
        volume=0.7
    )


# ============================================================
# /JOIN
# ============================================================

@bot.tree.command(
    name="join",
    description="Hace que el bot entre a tu canal de voz."
)
async def join(
    interaction: discord.Interaction
):

    try:

        await interaction.response.defer()

    except discord.NotFound:

        return


    guild = interaction.guild


    if guild is None:

        await interaction.followup.send(
            "❌ Este comando solo funciona "
            "dentro de un servidor."
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
            f"🔊 Conectado a **{channel.name}**.\n"
            "🎧 Me quedaré conectado "
            "hasta usar `/leave`."
        )


    except Exception as e:

        await interaction.followup.send(
            f"❌ Error conectando a voz:\n`{e}`"
        )


# ============================================================
# /LEAVE
# ============================================================

@bot.tree.command(
    name="leave",
    description="Saca al bot del canal de voz."
)
async def leave(
    interaction: discord.Interaction
):

    try:

        await interaction.response.defer()

    except discord.NotFound:

        return


    guild = interaction.guild


    if guild is None:

        await interaction.followup.send(
            "❌ Este comando solo funciona "
            "dentro de un servidor."
        )

        return


    vc = guild.voice_client


    if not vc:

        await interaction.followup.send(
            "❌ No estoy conectado "
            "a ningún canal."
        )

        return


    try:

        if vc.is_playing():

            vc.stop()


        bot.queues[
            guild.id
        ].clear()


        await vc.disconnect(
            force=True
        )


        bot.join_channels.pop(
            guild.id,
            None
        )


        await interaction.followup.send(
            "👋 Salí del canal de voz."
        )


    except Exception as e:

        await interaction.followup.send(
            f"❌ Error saliendo:\n`{e}`"
        )


# ============================================================
# /PLAY
# ============================================================

@bot.tree.command(
    name="play",
    description="Reproduce música desde YouTube."
)
@app_commands.describe(
    link="Link o búsqueda de YouTube"
)
async def play(
    interaction: discord.Interaction,
    link: str
):

    try:

        await interaction.response.defer()

    except discord.NotFound:

        return


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


        try:

            data = await get_youtube_info(
                link
            )

        except Exception as e:

            await interaction.channel.send(
                "❌ No pude obtener el audio "
                "de YouTube.\n\n"
                f"`{e}`"
            )

            return


        audio_url = data.get(
            "url"
        )


        # NUEVO: cabeceras HTTP que yt-dlp usó para esta extracción,
        # necesarias para que ffmpeg pueda leer el stream.
        http_headers = data.get(
            "http_headers",
            {}
        )


        title = data.get(
            "title",
            "Canción desconocida"
        )


        webpage = data.get(
            "webpage_url",
            link
        )


        if not audio_url:

            await interaction.channel.send(
                "❌ YouTube no entregó "
                "una URL de audio."
            )

            return


        if (
            vc.is_playing()
            or vc.is_paused()
        ):

            vc.stop()


        source = create_audio_source(
            audio_url,
            http_headers,
        )


        def after_play(error):

            if error:

                log.error(
                    "Error reproduciendo '%s': %s",
                    title,
                    error
                )

            # NUEVO: mostramos el stderr real de ffmpeg si hubo
            # un fallo silencioso (sin excepción pero sin audio).
            try:

                stderr_output = (
                    source.original.proc.stderr.read()
                    if hasattr(source, "original")
                    else None
                )

                if stderr_output:

                    text = stderr_output.decode(
                        errors="ignore"
                    ).strip()

                    if text:

                        log.warning(
                            "ffmpeg stderr para '%s':\n%s",
                            title,
                            text
                        )

            except Exception:

                pass

            if not error:

                log.info(
                    "Terminó: %s",
                    title
                )


        vc.play(
            source,
            after=after_play
        )


        await interaction.channel.send(
            f"🎵 **Reproduciendo**\n"
            f"**{title}**\n"
            f"🔊 Volumen: 70%\n"
            f"🔗 {webpage}"
        )


    except Exception as e:

        log.exception(
            "Error en /play"
        )


        try:

            await interaction.channel.send(
                f"❌ Error reproduciendo:\n`{e}`"
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
async def pause(
    interaction: discord.Interaction
):

    vc = (
        interaction.guild.voice_client
        if interaction.guild
        else None
    )


    if not vc:

        await interaction.response.send_message(
            "❌ No estoy conectado a voz."
        )

        return


    if not vc.is_playing():

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
    description="Continúa la música."
)
async def resume(
    interaction: discord.Interaction
):

    vc = (
        interaction.guild.voice_client
        if interaction.guild
        else None
    )


    if not vc:

        await interaction.response.send_message(
            "❌ No estoy conectado a voz."
        )

        return


    if not vc.is_paused():

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
    description="Detiene la música."
)
async def stop(
    interaction: discord.Interaction
):

    vc = (
        interaction.guild.voice_client
        if interaction.guild
        else None
    )


    if not vc:

        await interaction.response.send_message(
            "❌ No estoy conectado a voz."
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
        "🔊 Sigo conectado al canal."
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

    vc = (
        interaction.guild.voice_client
        if interaction.guild
        else None
    )


    if not vc:

        await interaction.response.send_message(
            "❌ No estoy conectado a voz."
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
async def queue(
    interaction: discord.Interaction
):

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
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    log.exception(
        "Error en slash command: %s",
        error
    )


    try:

        message = (
            "❌ Ocurrió un error ejecutando "
            "el comando.\n"
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

        print(
            "❌ FALTA DISCORD_TOKEN"
        )

        raise RuntimeError(
            "Configura DISCORD_TOKEN "
            "en Railway."
        )


    try:

        bot.run(
            TOKEN
        )


    except KeyboardInterrupt:

        print(
            "\n🛑 Bot detenido."
        )


    except Exception as e:

        log.exception(
            "El bot terminó con error: %s",
            e
        )
