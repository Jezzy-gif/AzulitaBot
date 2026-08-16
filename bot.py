import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")
YOUTUBE_COOKIES = os.getenv("YOUTUBE_COOKIES", "")

FFMPEG_PATH = shutil.which("ffmpeg") or "ffmpeg"
COOKIES_FILE = "/tmp/youtube_cookies.txt"

EXTRACT_TIMEOUT = 35


# ============================================================
# LOG
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

        log.info("🍪 Cookies de YouTube cargadas.")

    except Exception as e:
        log.error("No se pudieron guardar las cookies: %s", e)


# ============================================================
# COMPROBACIONES
# ============================================================

print("======================================")
print("🚀 INICIANDO AZULITA")
print("======================================")
print("Python:", os.sys.version)
print("discord.py:", discord.__version__)
print("yt-dlp:", yt_dlp.version.__version__)
print("FFmpeg:", FFMPEG_PATH)

if shutil.which("ffmpeg") is None and not os.path.isfile(FFMPEG_PATH):
    print("❌ FFmpeg no encontrado.")
    raise RuntimeError("FFmpeg no está instalado.")

print("✅ FFmpeg encontrado.")

if os.path.isfile(COOKIES_FILE):
    print("🍪 Cookies activadas.")
else:
    print("⚠️ Sin cookies de YouTube.")

print("======================================")


# ============================================================
# INTENTS
# ============================================================

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True


# ============================================================
# SONG
# ============================================================

@dataclass
class Song:

    title: str
    webpage_url: str
    audio_url: str
    duration: int = 0
    requester: str = ""


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

        self.play_locks = defaultdict(
            asyncio.Lock
        )

        self.players = {}

        self.join_channels = {}

    async def setup_hook(self):

        try:

            synced = await self.tree.sync()

            log.info(
                "✅ Comandos sincronizados: %s",
                len(synced)
            )

        except Exception:

            log.exception(
                "Error sincronizando comandos"
            )

    async def on_ready(self):

        log.info(
            "======================================"
        )

        log.info(
            "🟢 BOT CONECTADO: %s",
            self.user
        )

        log.info(
            "discord.py: %s",
            discord.__version__
        )

        log.info(
            "yt-dlp: %s",
            yt_dlp.version.__version__
        )

        log.info(
            "======================================")


bot = MusicBot()


# ============================================================
# YT-DLP BASE
# ============================================================

BASE_OPTIONS = {

    "format": "bestaudio/best",

    "noplaylist": True,

    "quiet": True,

    "no_warnings": True,

    "default_search": "ytsearch1",

    "source_address": "0.0.0.0",

    "extract_flat": False,

    "skip_download": True,

    "retries": 2,

    "fragment_retries": 2,

    "socket_timeout": 15,

    "nocheckcertificate": True,

    "geo_bypass": True,

}

if os.path.isfile(COOKIES_FILE):

    BASE_OPTIONS["cookiefile"] = COOKIES_FILE


# ============================================================
# EXTRAER UNA CANCIÓN
# ============================================================

async def extract_song(
    query: str,
    requester: str = ""
):

    loop = asyncio.get_running_loop()

    options = BASE_OPTIONS.copy()

    def extract():

        with yt_dlp.YoutubeDL(options) as ydl:

            data = ydl.extract_info(
                query,
                download=False
            )

            return data

    try:

        data = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                extract
            ),
            timeout=EXTRACT_TIMEOUT
        )

    except asyncio.TimeoutError:

        raise RuntimeError(
            "YouTube tardó demasiado en responder."
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

    audio_url = data.get("url")

    if not audio_url:

        raise RuntimeError(
            "YouTube no devolvió una URL de audio."
        )

    return Song(
        title=data.get(
            "title",
            "Canción desconocida"
        ),
        webpage_url=data.get(
            "webpage_url",
            query
        ),
        audio_url=audio_url,
        duration=data.get(
            "duration",
            0
        ) or 0,
        requester=requester
    )


# ============================================================
# EXTRAER PLAYLIST
# ============================================================

async def extract_playlist(
    url: str,
    requester: str = ""
):

    loop = asyncio.get_running_loop()

    options = BASE_OPTIONS.copy()

    options["noplaylist"] = False
    options["extract_flat"] = True

    def extract():

        with yt_dlp.YoutubeDL(options) as ydl:

            return ydl.extract_info(
                url,
                download=False
            )

    try:

        data = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                extract
            ),
            timeout=EXTRACT_TIMEOUT
        )

    except asyncio.TimeoutError:

        raise RuntimeError(
            "La playlist tardó demasiado en responder."
        )

    if not data:

        raise RuntimeError(
            "YouTube no devolvió información."
        )

    entries = data.get("entries") or []

    songs = []

    # Limitar para evitar colas gigantes
    entries = entries[:50]

    for entry in entries:

        if not entry:
            continue

        webpage = (
            entry.get("webpage_url")
            or entry.get("url")
        )

        title = entry.get(
            "title",
            "Canción desconocida"
        )

        if not webpage:
            continue

        # Extraemos posteriormente el audio real
        try:

            song = await extract_song(
                webpage,
                requester
            )

            songs.append(song)

        except Exception as e:

            log.warning(
                "No se pudo agregar '%s': %s",
                title,
                e
            )

    return songs


# ============================================================
# VOZ
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
                    "Error moviendo canal"
                )

                raise

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
                "🔊 Conectado a %s",
                channel.name
            )

            return vc

        except Exception as e:

            raise RuntimeError(
                f"No pude conectarme a voz: {e}"
            )


# ============================================================
# AUDIO
# ============================================================

def create_audio_source(
    audio_url: str
):

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
        volume=0.70
    )


# ============================================================
# REPRODUCIR SIGUIENTE
# ============================================================

async def play_next(
    guild: discord.Guild
):

    async with bot.play_locks[guild.id]:

        vc = guild.voice_client

        if not vc or not vc.is_connected():
            return

        if vc.is_playing():
            return

        queue = bot.queues[guild.id]

        if not queue:

            log.info(
                "📭 Cola vacía en %s",
                guild.name
            )

            return

        song = queue.pop(0)

        try:

            source = create_audio_source(
                song.audio_url
            )

            finished = asyncio.Event()

            def after(error):

                if error:

                    log.error(
                        "Error reproduciendo %s: %s",
                        song.title,
                        error
                    )

                finished.set()

            vc.play(
                source,
                after=after
            )

            log.info(
                "🎵 Reproduciendo: %s",
                song.title
            )

            # Esperar sin bloquear Discord
            asyncio.create_task(
                wait_next(
                    guild,
                    finished
                )
            )

        except Exception as e:

            log.exception(
                "Error iniciando canción"
            )

            asyncio.create_task(
                play_next(guild)
            )


async def wait_next(
    guild: discord.Guild,
    finished: asyncio.Event
):

    try:

        await finished.wait()

    except Exception:
        pass

    await asyncio.sleep(0.5)

    if guild.voice_client:

        await play_next(guild)


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

    try:

        await connect_to_voice(
            guild,
            member.voice.channel
        )

        bot.join_channels[
            guild.id
        ] = member.voice.channel.id

        await interaction.followup.send(
            f"🔊 Conectado a **{member.voice.channel.name}**."
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
    description="Reproduce o agrega una canción."
)
@app_commands.describe(
    link="Link o nombre de la canción"
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

    try:

        vc = await connect_to_voice(
            guild,
            member.voice.channel
        )

        bot.join_channels[
            guild.id
        ] = member.voice.channel.id

        await interaction.followup.send(
            "🔎 Buscando..."
        )

        song = await extract_song(
            link,
            str(member)
        )

        bot.queues[
            guild.id
        ].append(song)

        position = len(
            bot.queues[guild.id]
        )

        await interaction.channel.send(
            f"🎵 **Agregado a la cola**\n"
            f"**{song.title}**\n"
            f"📍 Posición: `{position}`"
        )

        if not vc.is_playing():

            await play_next(guild)

    except Exception as e:

        log.exception(
            "Error /play"
        )

        await interaction.channel.send(
            f"❌ Error:\n`{e}`"
        )


# ============================================================
# /ADD
# ============================================================

@bot.tree.command(
    name="add",
    description="Agrega una canción a la cola."
)
@app_commands.describe(
    link="Link o búsqueda"
)
async def add(
    interaction: discord.Interaction,
    link: str
):

    await interaction.response.defer()

    guild = interaction.guild

    if not guild:
        return

    try:

        song = await extract_song(
            link,
            str(interaction.user)
        )

        bot.queues[
            guild.id
        ].append(song)

        position = len(
            bot.queues[guild.id]
        )

        await interaction.followup.send(
            f"➕ **Agregado a la cola**\n"
            f"🎵 {song.title}\n"
            f"📍 Posición: `{position}`"
        )

        vc = guild.voice_client

        if vc and not vc.is_playing():

            await play_next(guild)

    except Exception as e:

        await interaction.followup.send(
            f"❌ No se pudo agregar:\n`{e}`"
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
        return

    songs = bot.queues[guild.id]

    if not songs:

        await interaction.response.send_message(
            "📭 La cola está vacía."
        )

        return

    text = "\n".join(
        f"`{i + 1}` 🎵 {song.title}"
        for i, song in enumerate(
            songs[:25]
        )
    )

    await interaction.response.send_message(
        f"🎶 **Cola actual**\n\n{text}"
    )


# ============================================================
# /SKIP
# ============================================================

@bot.tree.command(
    name="skip",
    description="Salta la canción."
)
async def skip(
    interaction: discord.Interaction
):

    guild = interaction.guild

    if not guild:
        return

    vc = guild.voice_client

    if not vc or not vc.is_playing():

        await interaction.response.send_message(
            "❌ No hay ninguna canción."
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
async def pause(
    interaction: discord.Interaction
):

    vc = (
        interaction.guild.voice_client
        if interaction.guild
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
async def resume(
    interaction: discord.Interaction
):

    vc = (
        interaction.guild.voice_client
        if interaction.guild
        else None
    )

    if not vc or not vc.is_paused():

        await interaction.response.send_message(
            "❌ No hay música pausada."
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
    description="Detiene la canción pero mantiene el bot."
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
            "❌ No estoy conectado."
        )

        return

    if vc.is_playing() or vc.is_paused():

        vc.stop()

    await interaction.response.send_message(
        "⏹️ Música detenida. "
        "🔊 Sigo conectado."
    )


# ============================================================
# /LEAVE
# ============================================================

@bot.tree.command(
    name="leave",
    description="Saca al bot del canal."
)
async def leave(
    interaction: discord.Interaction
):

    guild = interaction.guild

    if not guild:
        return

    vc = guild.voice_client

    if not vc:

        await interaction.response.send_message(
            "❌ No estoy conectado."
        )

        return

    bot.queues[guild.id].clear()

    if vc.is_playing():
        vc.stop()

    await vc.disconnect(
        force=True
    )

    bot.join_channels.pop(
        guild.id,
        None
    )

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

    log.exception(
        "Error slash command: %s",
        error
    )

    try:

        msg = f"❌ Error:\n`{error}`"

        if interaction.response.is_done():

            await interaction.followup.send(
                msg,
                ephemeral=True
            )

        else:

            await interaction.response.send_message(
                msg,
                ephemeral=True
            )

    except Exception:
        pass


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    if not TOKEN:

        raise RuntimeError(
            "❌ Falta DISCORD_TOKEN."
        )

    print(
        "🚀 Iniciando bot..."
    )

    bot.run(TOKEN)
