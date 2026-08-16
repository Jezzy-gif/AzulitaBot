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
FFMPEG_PATH = "ffmpeg"


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

print()
print("======================================")
print("🔍 COMPROBANDO INSTALACIÓN")
print("======================================")
print(f"🐍 Python: {sys.version}")
print(f"📦 discord.py: {discord.__version__}")
print(f"🎧 FFmpeg: {FFMPEG_PATH}")

if shutil.which(FFMPEG_PATH) is None:
    print("❌ NO SE ENCONTRÓ FFmpeg")
    sys.exit(1)

print("✅ FFmpeg encontrado.")
try:
    import davey
    print(
        f"🔐 davey: "
        f"{getattr(davey, '__version__', 'instalado')}"
    )
except ImportError:
    print("❌ davey no está instalado.")
    print("Ejecuta:")
    print("pip install -U davey")
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
            "FFmpeg: %s",
            FFMPEG_PATH
        )
        log.info("======================================")


bot = MusicBot()


# ============================================================
# YT-DLP
# ============================================================

YTDLP_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
    "cookiefile": None,
    "skip_download": True,
}

YTDLP = yt_dlp.YoutubeDL(
    YTDLP_OPTIONS
)


async def get_youtube_info(query: str):

    loop = asyncio.get_running_loop()

    def extract():

        return YTDLP.extract_info(
            query,
            download=False
        )

    data = await loop.run_in_executor(
        None,
        extract
    )

    if not data:
        raise RuntimeError(
            "No se pudo obtener información."
        )

    if "entries" in data:

        entries = data.get("entries")

        if not entries:
            raise RuntimeError(
                "No se encontró ninguna canción."
            )

        data = entries[0]

    return data


# ============================================================
# CONEXIÓN A VOZ
# ============================================================

async def connect_to_voice(
    guild: discord.Guild,
    channel: discord.VoiceChannel
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
                    "Error moviendo el bot de canal."
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
                timeout=30
            )

            log.info(
                "Conectado a voz: %s",
                channel.name
            )

            return vc

        except asyncio.TimeoutError:

            raise RuntimeError(
                "Discord tardó demasiado "
                "en conectar al canal de voz."
            )

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
            "❌ No pude comprobar "
            "tu canal de voz."
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

        bot.join_channels[guild.id] = channel.id

        await interaction.followup.send(
            f"🔊 Conectado a **{channel.name}**.\n"
            f"🎧 Me quedaré conectado "
            f"hasta usar `/leave`."
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

        bot.queues[guild.id].clear()

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
            f"❌ Error saliendo de voz:\n`{e}`"
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
            "❌ Entra primero a un canal de voz."
        )

        return

    channel = member.voice.channel

    try:

        vc = await connect_to_voice(
            guild,
            channel
        )

        bot.join_channels[guild.id] = channel.id

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
                "de YouTube.\n"
                f"`{e}`"
            )

            return

        audio_url = data.get("url")

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
            audio_url
        )

        def after_play(error):

            if error:

                log.error(
                    "Error reproduciendo '%s': %s",
                    title,
                    error
                )

            else:

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

    songs = bot.queues[guild.id]

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
# ERRORES DE SLASH COMMANDS
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

    print("🚀 Iniciando bot...")

    if not TOKEN:

        print()
        print("❌ FALTA DISCORD_TOKEN")
        print()
        print(
            "Configura DISCORD_TOKEN "
            "en las Variables de Railway."
        )
        print()

        raise RuntimeError(
            "Falta DISCORD_TOKEN."
        )

    try:

        bot.run(TOKEN)

    except KeyboardInterrupt:

        print("\n🛑 Bot detenido.")

    except Exception as e:

        log.exception(
            "El bot terminó con error: %s",
            e
        )
