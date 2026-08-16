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

FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")

YOUTUBE_COOKIES = os.getenv("YOUTUBE_COOKIES", "").strip()

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

if YOUTUBE_COOKIES:

    try:
        with open(
            COOKIES_FILE,
            "w",
            encoding="utf-8",
            newline="\n"
        ) as f:
            f.write(YOUTUBE_COOKIES)

        os.chmod(COOKIES_FILE, 0o600)

        log.info("🍪 Cookies de YouTube cargadas.")

    except Exception as e:
        log.error("No se pudieron guardar las cookies: %s", e)

else:
    log.warning("⚠️ YOUTUBE_COOKIES no está configurada.")


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
    print("❌ FFmpeg no está disponible.")
    print("Instala FFmpeg en Railway o usa una imagen que lo incluya.")
    sys.exit(1)

print("✅ FFmpeg encontrado.")

if os.path.isfile(COOKIES_FILE):
    print("🍪 Cookies configuradas.")
else:
    print("⚠️ Funcionando sin cookies.")

try:
    import davey
    print(
        f"🔐 davey: "
        f"{getattr(davey, '__version__', 'instalado')}"
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
# DATACLASS DE CANCIÓN
# ============================================================

@dataclass
class Song:
    title: str
    webpage_url: str
    audio_url: str


# ============================================================
# BOT
# ============================================================

class MusicBot(commands.Bot):

    def __init__(self):

        super().__init__(
            command_prefix="!",
            intents=intents
        )

        # Cola por servidor
        self.queues = defaultdict(list)

        # Canción actual
        self.current = {}

        # Locks de voz
        self.voice_locks = defaultdict(
            asyncio.Lock
        )

        # Canal donde debe permanecer
        self.join_channels = {}

        # Locks de reproducción
        self.play_locks = defaultdict(
            asyncio.Lock
        )

    async def setup_hook(self):

        try:

            synced = await self.tree.sync()

            log.info(
                "Comandos sincronizados: %s",
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

    "noplaylist": False,

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

    "socket_timeout": 30,

    "ignoreerrors": True,

    "playlistend": 100,

}

if os.path.isfile(COOKIES_FILE):

    BASE_YTDLP_OPTIONS["cookiefile"] = COOKIES_FILE


# ============================================================
# EXTRAER INFORMACIÓN
# ============================================================

async def extract_youtube(query: str):

    loop = asyncio.get_running_loop()

    options = BASE_YTDLP_OPTIONS.copy()

    def extract():

        with yt_dlp.YoutubeDL(options) as ydl:

            return ydl.extract_info(
                query,
                download=False
            )

    return await loop.run_in_executor(
        None,
        extract
    )


# ============================================================
# CONVERTIR RESULTADO EN CANCIONES
# ============================================================

async def get_songs(query: str):

    try:

        data = await extract_youtube(query)

    except Exception as e:

        raise RuntimeError(
            f"No se pudo obtener la canción o playlist.\n\n"
            f"Último error: {e}"
        )

    if not data:

        raise RuntimeError(
            "YouTube no devolvió información."
        )

    songs = []

    # --------------------------------------------------------
    # PLAYLIST
    # --------------------------------------------------------

    if data.get("_type") == "playlist" or "entries" in data:

        entries = data.get("entries") or []

        playlist_title = data.get(
            "title",
            "Playlist"
        )

        for entry in entries:

            if not entry:
                continue

            # Algunas playlists devuelven URLs incompletas
            webpage = (
                entry.get("webpage_url")
                or entry.get("original_url")
                or entry.get("url")
            )

            title = entry.get(
                "title",
                "Canción desconocida"
            )

            if not webpage:
                continue

            # Si es una URL de vídeo, necesitamos
            # extraer nuevamente para conseguir audio_url.
            try:

                song_data = await extract_youtube(
                    webpage
                )

                if not song_data:
                    continue

                audio_url = song_data.get("url")

                if not audio_url:
                    continue

                songs.append(
                    Song(
                        title=song_data.get(
                            "title",
                            title
                        ),
                        webpage_url=song_data.get(
                            "webpage_url",
                            webpage
                        ),
                        audio_url=audio_url
                    )
                )

            except Exception as e:

                log.warning(
                    "No se pudo cargar '%s': %s",
                    title,
                    e
                )

        if not songs:

            raise RuntimeError(
                "La playlist no contiene canciones "
                "que se puedan reproducir."
            )

        log.info(
            "Playlist '%s': %s canciones",
            playlist_title,
            len(songs)
        )

        return songs, playlist_title

    # --------------------------------------------------------
    # CANCIÓN INDIVIDUAL
    # --------------------------------------------------------

    audio_url = data.get("url")

    if not audio_url:

        raise RuntimeError(
            "YouTube no entregó una URL de audio."
        )

    song = Song(
        title=data.get(
            "title",
            "Canción desconocida"
        ),
        webpage_url=data.get(
            "webpage_url",
            query
        ),
        audio_url=audio_url
    )

    return [song], None


# ============================================================
# CONECTAR A VOZ
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
                timeout=30
            )

            log.info(
                "Conectado a voz: %s",
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

def create_audio_source(song: Song):

    return discord.PCMVolumeTransformer(

        discord.FFmpegPCMAudio(

            song.audio_url,

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
# REPRODUCIR SIGUIENTE
# ============================================================

async def play_next(guild_id: int):

    guild = bot.get_guild(guild_id)

    if not guild:
        return

    async with bot.play_locks[guild_id]:

        vc = guild.voice_client

        if not vc or not vc.is_connected():
            return

        if vc.is_playing() or vc.is_paused():
            return

        if not bot.queues[guild_id]:

            bot.current.pop(
                guild_id,
                None
            )

            return

        song = bot.queues[guild_id].pop(0)

        bot.current[guild_id] = song

        try:

            source = create_audio_source(
                song
            )

            def after(error):

                if error:

                    log.error(
                        "Error reproduciendo %s: %s",
                        song.title,
                        error
                    )

                else:

                    log.info(
                        "Terminó: %s",
                        song.title
                    )

                asyncio.run_coroutine_threadsafe(
                    play_next(guild_id),
                    bot.loop
                )

            vc.play(
                source,
                after=after
            )

            log.info(
                "🎵 Reproduciendo: %s",
                song.title
            )

        except Exception as e:

            log.exception(
                "Error creando audio."
            )

            await play_next(guild_id)


# ============================================================
# /JOIN
# ============================================================

@bot.tree.command(
    name="join",
    description="Entra a tu canal de voz."
)
async def join(interaction: discord.Interaction):

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
# /PLAY
# ============================================================

@bot.tree.command(
    name="play",
    description="Reproduce una canción o playlist."
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
            "🔎 Buscando canción/playlist..."
        )

        songs, playlist_title = await get_songs(
            link
        )

        bot.queues[guild.id].extend(
            songs
        )

        # Si no estaba reproduciendo nada,
        # comienza inmediatamente.
        if not vc.is_playing() and not vc.is_paused():

            await play_next(
                guild.id
            )

        if playlist_title:

            await interaction.channel.send(
                f"📀 **Playlist agregada:** "
                f"**{playlist_title}**\n"
                f"🎵 Canciones agregadas: "
                f"**{len(songs)}**"
            )

        else:

            await interaction.channel.send(
                f"🎵 **Agregada:** "
                f"**{songs[0].title}**"
            )

    except Exception as e:

        log.exception(
            "Error en /play"
        )

        await interaction.channel.send(
            f"❌ **Error:**\n\n"
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

    if guild is None:

        await interaction.response.send_message(
            "❌ Solo funciona en servidores."
        )

        return

    songs = bot.queues[guild.id]

    current = bot.current.get(
        guild.id
    )

    text = ""

    if current:

        text += (
            f"▶️ **Reproduciendo:** "
            f"{current.title}\n\n"
        )

    if not songs:

        if text:

            await interaction.response.send_message(
                text +
                "📭 No hay más canciones en cola."
            )

        else:

            await interaction.response.send_message(
                "📭 La cola está vacía."
            )

        return

    for i, song in enumerate(
        songs[:20],
        start=1
    ):

        text += (
            f"**{i}.** {song.title}\n"
        )

    if len(songs) > 20:

        text += (
            f"\n... y {len(songs) - 20} más."
        )

    await interaction.response.send_message(
        f"🎵 **Cola:**\n\n{text}"
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

    vc = (
        guild.voice_client
        if guild
        else None
    )

    if not vc:

        await interaction.response.send_message(
            "❌ No estoy conectado a voz."
        )

        return

    if not vc.is_playing():

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

    guild = interaction.guild

    vc = (
        guild.voice_client
        if guild
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

    guild = interaction.guild

    vc = (
        guild.voice_client
        if guild
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
    description="Detiene la música pero mantiene el bot en voz."
)
async def stop(
    interaction: discord.Interaction
):

    guild = interaction.guild

    vc = (
        guild.voice_client
        if guild
        else None
    )

    if not vc:

        await interaction.response.send_message(
            "❌ No estoy conectado a voz."
        )

        return

    bot.queues[guild.id].clear()

    if vc.is_playing() or vc.is_paused():

        vc.stop()

    bot.current.pop(
        guild.id,
        None
    )

    await interaction.response.send_message(
        "⏹️ Música detenida.\n"
        "🔊 Sigo conectado al canal."
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

    if guild is None:

        await interaction.response.send_message(
            "❌ Solo funciona en servidores."
        )

        return

    vc = guild.voice_client

    if not vc:

        await interaction.response.send_message(
            "❌ No estoy conectado a voz."
        )

        return

    bot.queues[guild.id].clear()

    bot.current.pop(
        guild.id,
        None
    )

    bot.join_channels.pop(
        guild.id,
        None
    )

    try:

        if vc.is_playing():
            vc.stop()

        await vc.disconnect(
            force=True
        )

        await interaction.response.send_message(
            "👋 Salí del canal de voz."
        )

    except Exception as e:

        await interaction.response.send_message(
            f"❌ Error:\n`{e}`"
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
        "Error de slash command: %s",
        error
    )

    try:

        message = (
            "❌ Ocurrió un error.\n"
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

    print("🚀 Iniciando Azulita...")

    if not TOKEN:

        raise RuntimeError(
            "Falta DISCORD_TOKEN en Railway."
        )

    try:

        bot.run(TOKEN)

    except KeyboardInterrupt:

        print("🛑 Bot detenido.")

    except Exception as e:

        log.exception(
            "El bot terminó con error: %s",
            e
        )
