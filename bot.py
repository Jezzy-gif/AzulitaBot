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

YOUTUBE_COOKIES = os.getenv(
    "YOUTUBE_COOKIES",
    ""
).strip()

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

if YOUTUBE_COOKIES:

    try:

        with open(
            COOKIES_FILE,
            "w",
            encoding="utf-8",
            newline="\n"
        ) as f:

            f.write(YOUTUBE_COOKIES)

        os.chmod(
            COOKIES_FILE,
            0o600
        )

        print("🍪 Cookies de YouTube cargadas.")

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


if shutil.which(FFMPEG_PATH) is None:

    print(
        "❌ NO SE ENCONTRÓ FFmpeg"
    )

    print(
        "Instala FFmpeg o configura FFMPEG_PATH."
    )

    sys.exit(1)


print(
    "✅ FFmpeg encontrado."
)


try:

    import nacl

    print(
        "🔊 PyNaCl: instalado"
    )

except ImportError:

    print(
        "❌ PyNaCl no está instalado."
    )

    print(
        "Instala: pip install PyNaCl"
    )

    sys.exit(1)


try:

    import davey

    print(
        "🔐 davey:",
        getattr(
            davey,
            "__version__",
            "instalado"
        )
    )

except ImportError:

    print(
        "⚠️ davey no está instalado."
    )


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

        # Cola por servidor
        self.queues = defaultdict(list)

        # Canción actual por servidor
        self.current = {}

        # Canal donde el bot debe permanecer
        self.join_channels = {}

        # Locks de conexión
        self.voice_locks = defaultdict(
            asyncio.Lock
        )

        # Lock del reproductor
        self.player_locks = defaultdict(
            asyncio.Lock
        )

        # Tarea de reproducción por servidor
        self.player_tasks = {}


    async def setup_hook(self):

        try:

            synced = await self.tree.sync()

            log.info(
                "✅ Comandos sincronizados: %s",
                len(synced)
            )

        except Exception as e:

            log.exception(
                "❌ Error sincronizando comandos: %s",
                e
            )


    async def on_ready(self):

        log.info(
            "======================================"
        )

        log.info(
            "🤖 BOT CONECTADO: %s",
            self.user
        )

        log.info(
            "📦 Discord.py: %s",
            discord.__version__
        )

        log.info(
            "📦 yt-dlp: %s",
            yt_dlp.version.__version__
        )

        log.info(
            "🎧 FFmpeg: %s",
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

    "format": "bestaudio[ext=webm]/bestaudio/best",

    "noplaylist": True,

    "quiet": True,

    "no_warnings": True,

    "default_search": "ytsearch",

    "source_address": "0.0.0.0",

    "extract_flat": False,

    "skip_download": True,

    "retries": 5,

    "fragment_retries": 5,

    "socket_timeout": 30,

    "concurrent_fragment_downloads": 1,
}


if os.path.isfile(COOKIES_FILE):

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
# EXTRAER CON CLIENT
# ============================================================

async def extract_with_client(
    query: str,
    client: str
):

    loop = asyncio.get_running_loop()

    options = BASE_YTDLP_OPTIONS.copy()

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


# ============================================================
# OBTENER INFORMACIÓN DE YOUTUBE
# ============================================================

async def get_youtube_info(
    query: str
):

    last_error = None


    # ========================================================
    # CLIENTES
    # ========================================================

    for client in YOUTUBE_CLIENTS:

        try:

            log.info(
                "🔎 Probando YouTube client: %s",
                client
            )

            data = await extract_with_client(
                query,
                client
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


            if not data:
                continue


            if data.get("url"):

                log.info(
                    "✅ YouTube funcionó con: %s",
                    client
                )

                return data


        except Exception as e:

            last_error = e

            log.warning(
                "⚠️ Cliente %s falló: %s",
                client,
                e
            )


    # ========================================================
    # INTENTO AUTOMÁTICO
    # ========================================================

    try:

        log.info(
            "🔎 Probando extracción automática..."
        )

        options = BASE_YTDLP_OPTIONS.copy()


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


            if data and data.get("url"):

                return data


    except Exception as e:

        last_error = e


    # ========================================================
    # ERROR
    # ========================================================

    if last_error:

        raise RuntimeError(
            "YouTube no permitió obtener el audio.\n"
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


        # ====================================================
        # YA ESTÁ EN EL CANAL
        # ====================================================

        if vc and vc.is_connected():

            if (
                vc.channel
                and vc.channel.id == channel.id
            ):

                return vc


            # Intentar mover
            try:

                await vc.move_to(
                    channel
                )

                return vc

            except Exception as e:

                log.error(
                    "❌ Error moviendo bot: %s",
                    e
                )


        # ====================================================
        # DESCONECTAR CONEXIÓN ANTERIOR
        # ====================================================

        if vc:

            try:

                await vc.disconnect(
                    force=True
                )

            except Exception:
                pass

            await asyncio.sleep(1)


        # ====================================================
        # CONECTAR
        # ====================================================

        try:

            vc = await channel.connect(
                reconnect=True,
                timeout=30
            )

            log.info(
                "🔊 Conectado a voz: %s",
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
                "❌ Error conectando a voz."
            )

            raise RuntimeError(
                f"No pude conectarme a voz: {e}"
            )


# ============================================================
# CREAR AUDIO SOURCE
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
            "-reconnect_delay_max 10 "
            "-nostdin"
        ),

        options=(
            "-vn "
            "-loglevel warning "
            "-ac 2 "
            "-ar 48000 "
            "-bufsize 512k"
        )
    )


    return discord.PCMVolumeTransformer(
        source,
        volume=0.7
    )


# ============================================================
# REPRODUCTOR DE COLA
# ============================================================

async def player_loop(
    guild: discord.Guild
):

    guild_id = guild.id


    while True:

        # ====================================================
        # COMPROBAR VOZ
        # ====================================================

        vc = guild.voice_client


        if not vc or not vc.is_connected():

            log.warning(
                "⚠️ Bot no está conectado a voz."
            )

            bot.current.pop(
                guild_id,
                None
            )

            return


        # ====================================================
        # SI NO HAY CANCIONES
        # ====================================================

        if not bot.queues[guild_id]:

            bot.current.pop(
                guild_id,
                None
            )

            log.info(
                "📭 Cola terminada en %s",
                guild.name
            )

            return


        # ====================================================
        # SACAR SIGUIENTE
        # ====================================================

        song = bot.queues[
            guild_id
        ].pop(0)


        bot.current[
            guild_id
        ] = song


        title = song[
            "title"
        ]

        query = song[
            "query"
        ]

        webpage = song[
            "webpage"
        ]


        log.info(
            "🎵 Preparando: %s",
            title
        )


        finished = asyncio.Event()

        loop = asyncio.get_running_loop()


        try:

            # =================================================
            # EXTRAER URL NUEVA
            # =================================================

            data = await get_youtube_info(
                query
            )


            audio_url = data.get(
                "url"
            )


            if not audio_url:

                raise RuntimeError(
                    "YouTube no entregó "
                    "una URL de audio."
                )


            title = data.get(
                "title",
                title
            )


            webpage = data.get(
                "webpage_url",
                webpage
            )


            # =================================================
            # CREAR SOURCE
            # =================================================

            source = create_audio_source(
                audio_url
            )


            # =================================================
            # CALLBACK DE FFmpeg
            # =================================================

            def after_play(error):

                if error:

                    log.error(
                        "❌ Error FFmpeg en '%s': %s",
                        title,
                        error
                    )

                else:

                    log.info(
                        "✅ Terminó: %s",
                        title
                    )


                # MUY IMPORTANTE:
                # after() corre en un hilo diferente.
                loop.call_soon_threadsafe(
                    finished.set
                )


            # =================================================
            # REPRODUCIR
            # =================================================

            if not vc.is_connected():

                raise RuntimeError(
                    "El bot se desconectó "
                    "antes de reproducir."
                )


            vc.play(
                source,
                after=after_play
            )


            log.info(
                "▶️ REPRODUCIENDO: %s",
                title
            )


            # =================================================
            # AVISAR AL CANAL
            # =================================================

            channel_id = bot.join_channels.get(
                guild_id
            )


            if channel_id:

                channel = guild.get_channel(
                    channel_id
                )

                if channel:

                    try:

                        await channel.send(
                            f"🎵 **Reproduciendo**\n"
                            f"**{title}**\n"
                            f"🔊 Volumen: **70%**\n"
                            f"🔗 {webpage}"
                        )

                    except Exception:
                        pass


            # =================================================
            # ESPERAR FINAL
            # =================================================

            await finished.wait()


        except asyncio.CancelledError:

            log.info(
                "🛑 Reproductor cancelado."
            )

            raise


        except Exception as e:

            log.exception(
                "❌ Error reproduciendo '%s': %s",
                title,
                e
            )


            channel_id = bot.join_channels.get(
                guild_id
            )


            if channel_id:

                channel = guild.get_channel(
                    channel_id
                )

                if channel:

                    try:

                        await channel.send(
                            f"❌ No pude reproducir "
                            f"**{title}**.\n"
                            f"`{e}`"
                        )

                    except Exception:
                        pass


        finally:

            bot.current.pop(
                guild_id,
                None
            )


        # ====================================================
        # PEQUEÑA PAUSA
        # ====================================================

        await asyncio.sleep(0.5)


# ============================================================
# INICIAR REPRODUCTOR
# ============================================================

def start_player(
    guild: discord.Guild
):

    task = bot.player_tasks.get(
        guild.id
    )


    if task and not task.done():

        return


    task = asyncio.create_task(
        player_loop(guild)
    )


    bot.player_tasks[
        guild.id
    ] = task


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

    await interaction.response.defer()


    guild = interaction.guild


    if guild is None:

        await interaction.followup.send(
            "❌ Este comando solo funciona "
            "en un servidor."
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
            f"❌ Error conectando:\n`{e}`"
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


    # ========================================================
    # CONECTAR
    # ========================================================

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
            f"❌ Error conectando a voz:\n`{e}`"
        )

        return


    # ========================================================
    # BUSCAR
    # ========================================================

    await interaction.followup.send(
        "🔎 Buscando la canción..."
    )


    try:

        data = await get_youtube_info(
            link
        )


    except Exception as e:

        await interaction.channel.send(
            "❌ No pude obtener la canción.\n\n"
            f"`{e}`"
        )

        return


    title = data.get(
        "title",
        "Canción desconocida"
    )


    webpage = data.get(
        "webpage_url",
        link
    )


    # ========================================================
    # AGREGAR A COLA
    # ========================================================

    song = {

        "query": webpage,

        "title": title,

        "webpage": webpage,

        "requester": interaction.user.id
    }


    was_playing = (
        vc.is_playing()
        or vc.is_paused()
        or bot.current.get(guild.id)
        is not None
        or bool(
            bot.queues[guild.id]
        )
    )


    bot.queues[
        guild.id
    ].append(song)


    # ========================================================
    # RESPUESTA
    # ========================================================

    if was_playing:

        position = len(
            bot.queues[guild.id]
        )


        await interaction.channel.send(
            f"📥 **Agregada a la cola**\n"
            f"🎵 **{title}**\n"
            f"📋 Posición: **{position}**"
        )

    else:

        await interaction.channel.send(
            f"🎵 **Añadida:**\n"
            f"**{title}**"
        )


    # ========================================================
    # INICIAR REPRODUCTOR
    # ========================================================

    start_player(
        guild
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
    description="Detiene la música y vacía la cola."
)
async def stop(
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


    # Vaciar cola
    bot.queues[
        guild.id
    ].clear()


    bot.current.pop(
        guild.id,
        None
    )


    if (
        vc.is_playing()
        or vc.is_paused()
    ):

        vc.stop()


    await interaction.response.send_message(
        "⏹️ Música detenida.\n"
        "📭 Cola vaciada.\n"
        "🔊 Sigo conectado."
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


    current = bot.current.get(
        guild.id
    )


    text = ""


    if current:

        text += (
            "▶️ **Reproduciendo ahora:**\n"
            f"🎵 {current['title']}\n\n"
        )


    if not songs:

        if text:

            await interaction.response.send_message(
                text
            )

        else:

            await interaction.response.send_message(
                "📭 La cola está vacía."
            )

        return


    text += (
        "📋 **Siguiente:**\n"
    )


    for i, song in enumerate(
        songs[:20],
        1
    ):

        text += (
            f"**{i}.** {song['title']}\n"
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
# /LEAVE
# ============================================================

@bot.tree.command(
    name="leave",
    description="Saca el bot del canal de voz."
)
async def leave(
    interaction: discord.Interaction
):

    await interaction.response.defer()


    guild = interaction.guild


    if guild is None:

        await interaction.followup.send(
            "❌ Solo funciona en servidores."
        )

        return


    vc = guild.voice_client


    if not vc:

        await interaction.followup.send(
            "❌ No estoy conectado "
            "a ningún canal."
        )

        return


    # Vaciar cola
    bot.queues[
        guild.id
    ].clear()


    bot.current.pop(
        guild.id,
        None
    )


    bot.join_channels.pop(
        guild.id,
        None
    )


    # Detener música
    if (
        vc.is_playing()
        or vc.is_paused()
    ):

        vc.stop()


    # Cancelar player task
    task = bot.player_tasks.get(
        guild.id
    )


    if task and not task.done():

        task.cancel()


    bot.player_tasks.pop(
        guild.id,
        None
    )


    try:

        await vc.disconnect(
            force=True
        )


    except Exception as e:

        await interaction.followup.send(
            f"❌ Error saliendo:\n`{e}`"
        )

        return


    await interaction.followup.send(
        "👋 Salí del canal de voz."
    )


# ============================================================
# AUTO RECONEXIÓN
# ============================================================

@bot.event
async def on_voice_state_update(
    member,
    before,
    after
):

    if not bot.user:
        return


    if member.id != bot.user.id:
        return


    guild = member.guild


    # ========================================================
    # BOT DESCONECTADO
    # ========================================================

    if before.channel and not after.channel:

        channel_id = bot.join_channels.get(
            guild.id
        )


        # Si fue /leave no reconectar
        if not channel_id:
            return


        await asyncio.sleep(3)


        channel = guild.get_channel(
            channel_id
        )


        if not channel:
            return


        try:

            await connect_to_voice(
                guild,
                channel
            )


            log.info(
                "🔄 Reconectado a %s",
                channel.name
            )


            if bot.queues[guild.id]:

                start_player(
                    guild
                )


        except Exception as e:

            log.error(
                "❌ No pude reconectar: %s",
                e
            )


# ============================================================
# ERRORES SLASH COMMANDS
# ============================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    log.exception(
        "❌ Error en slash command: %s",
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
        "🚀 Iniciando Azulita..."
    )


    if not TOKEN:

        print(
            "❌ FALTA DISCORD_TOKEN"
        )

        raise RuntimeError(
            "Configura DISCORD_TOKEN."
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
            "❌ El bot terminó con error: %s",
            e
        )
