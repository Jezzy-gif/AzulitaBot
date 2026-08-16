import asyncio
import logging
import os
import random
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

            f.write(YOUTUBE_COOKIES)

        try:
            os.chmod(
                COOKIES_FILE,
                0o600
            )
        except Exception:
            pass

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


ffmpeg_real = shutil.which(
    FFMPEG_PATH
)

if not ffmpeg_real:

    print(
        "❌ NO SE ENCONTRÓ FFmpeg."
    )

    print(
        "Instala FFmpeg o asegúrate de que "
        "esté disponible en PATH."
    )

    sys.exit(1)


FFMPEG_PATH = ffmpeg_real

print(
    f"✅ FFmpeg encontrado: {FFMPEG_PATH}"
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
        "🔐 davey:",
        getattr(
            davey,
            "__version__",
            "instalado"
        )
    )

except ImportError:

    print(
        "❌ davey no está instalado."
    )

    sys.exit(1)


try:

    import nacl

    print(
        "🔊 PyNaCl:",
        getattr(
            nacl,
            "__version__",
            "instalado"
        )
    )

except ImportError:

    print(
        "❌ PyNaCl no está instalado."
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

        # Cola por servidor
        self.queues = defaultdict(list)

        # Locks de voz
        self.voice_locks = defaultdict(
            asyncio.Lock
        )

        # Canal donde está conectado
        self.join_channels = {}

        # Canción actual
        self.current_song = {}

        # Repetición
        self.loop_mode = defaultdict(
            lambda: False
        )

        # Evita que dos reproducciones
        # comiencen al mismo tiempo
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
            "🤖 BOT CONECTADO: %s",
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

    "ignoreerrors": False,
}


if os.path.isfile(
    COOKIES_FILE
):

    BASE_YTDLP_OPTIONS[
        "cookiefile"
    ] = COOKIES_FILE


# ============================================================
# CLIENTES DE YOUTUBE
# ============================================================

YOUTUBE_CLIENTS = [

    "android_vr",

    "tv",

    "web_embedded",

    "mweb",

    "web_safari",

]


# ============================================================
# EXTRAER INFORMACIÓN
# ============================================================

async def extract_youtube(
    query,
    client=None,
    flat=False
):

    loop = asyncio.get_running_loop()

    options = BASE_YTDLP_OPTIONS.copy()

    options["extract_flat"] = flat

    if client:

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
# OBTENER UNA CANCIÓN
# ============================================================

async def get_youtube_info(
    query
):

    last_error = None

    for client in YOUTUBE_CLIENTS:

        try:

            log.info(
                "🔎 Probando YouTube client: %s",
                client
            )

            data = await extract_youtube(
                query,
                client=client,
                flat=False
            )

            if not data:
                continue


            # Si yt-dlp devuelve búsqueda
            if "entries" in data:

                entries = (
                    data.get("entries")
                    or []
                )

                if not entries:
                    continue

                data = entries[0]


            if data.get("url"):

                log.info(
                    "✅ Audio obtenido con %s",
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


    # Último intento
    try:

        log.info(
            "🔄 Último intento automático..."
        )

        data = await extract_youtube(
            query,
            client=None,
            flat=False
        )

        if data:

            if "entries" in data:

                entries = (
                    data.get("entries")
                    or []
                )

                if entries:

                    data = entries[0]


            if data.get("url"):

                return data


    except Exception as e:

        last_error = e


    raise RuntimeError(
        "No se pudo obtener el audio de YouTube.\n"
        f"Último error: {last_error}"
    )


# ============================================================
# OBTENER CANCIÓN O PLAYLIST
# ============================================================

async def get_playlist_items(
    query
):

    last_error = None

    for client in YOUTUBE_CLIENTS:

        try:

            log.info(
                "📃 Buscando con cliente: %s",
                client
            )

            data = await extract_youtube(
                query,
                client=client,
                flat=True
            )

            if not data:
                continue


            # ==================================================
            # PLAYLIST
            # ==================================================

            if (
                data.get("_type")
                == "playlist"
            ):

                entries = (
                    data.get("entries")
                    or []
                )

                songs = []

                for entry in entries:

                    if not entry:
                        continue

                    url = (
                        entry.get(
                            "webpage_url"
                        )
                        or entry.get(
                            "original_url"
                        )
                        or entry.get(
                            "url"
                        )
                    )

                    title = entry.get(
                        "title",
                        "Canción desconocida"
                    )


                    if not url:
                        continue


                    # Convertir URL cuando
                    # yt-dlp entrega ID
                    if (
                        not url.startswith(
                            "http://"
                        )
                        and not url.startswith(
                            "https://"
                        )
                    ):

                        url = (
                            "https://www.youtube.com/watch?v="
                            + url
                        )


                    songs.append({

                        "title": title,

                        "url": url

                    })


                if songs:

                    return songs


            # ==================================================
            # CANCIÓN INDIVIDUAL
            # ==================================================

            title = data.get(
                "title",
                "Canción desconocida"
            )

            url = (
                data.get(
                    "webpage_url"
                )
                or data.get(
                    "original_url"
                )
                or query
            )


            return [{

                "title": title,

                "url": url

            }]


        except Exception as e:

            last_error = e

            log.warning(
                "⚠️ Cliente %s falló: %s",
                client,
                e
            )


    raise RuntimeError(
        "No se pudo obtener la canción o playlist.\n"
        f"Último error: {last_error}"
    )


# ============================================================
# CONECTAR A VOZ
# ============================================================

async def connect_to_voice(
    guild,
    channel
):

    lock = bot.voice_locks[
        guild.id
    ]

    async with lock:

        vc = guild.voice_client


        # Ya está conectado
        if vc and vc.is_connected():

            if (
                vc.channel
                and vc.channel.id
                == channel.id
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


        # Conexión vieja
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
# CREAR AUDIO
# ============================================================

def create_audio_source(
    audio_url
):

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
# CANAL DE TEXTO PARA AVISOS
# ============================================================

def get_text_channel(
    guild
):

    # Primero intenta encontrar un canal
    # donde el bot pueda escribir.

    for channel in guild.text_channels:

        try:

            if channel.permissions_for(
                guild.me
            ).send_messages:

                return channel

        except Exception:
            continue


    return None


# ============================================================
# REPRODUCIR SIGUIENTE
# ============================================================

async def play_next(
    guild_id
):

    guild = bot.get_guild(
        guild_id
    )

    if not guild:
        return


    vc = guild.voice_client

    if not vc or not vc.is_connected():
        return


    async with bot.play_locks[
        guild_id
    ]:

        queue = bot.queues[
            guild_id
        ]


        # ====================================================
        # SI NO HAY COLA
        # ====================================================

        if not queue:

            bot.current_song.pop(
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

        song = queue.pop(0)

        title = song[
            "title"
        ]

        url = song[
            "url"
        ]


        # ====================================================
        # LOOP
        # ====================================================

        bot.current_song[
            guild_id
        ] = song


        try:

            log.info(
                "🔄 Extrayendo audio nuevamente: %s",
                title
            )


            data = await get_youtube_info(
                url
            )


            audio_url = data.get(
                "url"
            )


            if not audio_url:

                raise RuntimeError(
                    "YouTube no entregó "
                    "una URL de audio."
                )


            # =================================================
            # CREAR SOURCE
            # =================================================

            source = create_audio_source(
                audio_url
            )


            # =================================================
            # CALLBACK
            # =================================================

            def after_play(
                error
            ):

                if error:

                    log.error(
                        "❌ Error reproduciendo '%s': %s",
                        title,
                        error
                    )

                else:

                    log.info(
                        "✅ Terminó: %s",
                        title
                    )


                try:

                    future = (
                        asyncio.run_coroutine_threadsafe(
                            song_finished(
                                guild_id,
                                song
                            ),
                            bot.loop
                        )
                    )

                except Exception as callback_error:

                    log.error(
                        "Error callback: %s",
                        callback_error
                    )


            # =================================================
            # REPRODUCIR
            # =================================================

            vc.play(
                source,
                after=after_play
            )


            log.info(
                "🎵 Reproduciendo: %s",
                title
            )


            # =================================================
            # AVISO
            # =================================================

            text_channel = get_text_channel(
                guild
            )

            if text_channel:

                await text_channel.send(
                    f"🎵 **Reproduciendo**\n"
                    f"**{title}**\n"
                    f"🔊 Volumen: 70%"
                )


        except Exception as e:

            log.exception(
                "❌ Error reproduciendo %s",
                title
            )


            text_channel = get_text_channel(
                guild
            )

            if text_channel:

                try:

                    await text_channel.send(
                        f"❌ No pude reproducir "
                        f"**{title}**.\n"
                        f"`{e}`\n"
                        f"⏭️ Pasando a la siguiente..."
                    )

                except Exception:
                    pass


            # Continuar automáticamente
            await play_next(
                guild_id
            )


# ============================================================
# CANCIÓN TERMINADA
# ============================================================

async def song_finished(
    guild_id,
    song
):

    # ========================================================
    # LOOP ACTIVADO
    # ========================================================

    if bot.loop_mode[
        guild_id
    ]:

        bot.queues[
            guild_id
        ].insert(
            0,
            song
        )


    # ========================================================
    # SIGUIENTE
    # ========================================================

    await play_next(
        guild_id
    )


# ============================================================
# /JOIN
# ============================================================

@bot.tree.command(
    name="join",
    description="Entra a tu canal de voz."
)
async def join(
    interaction
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
            f"❌ Error conectando:\n`{e}`"
        )


# ============================================================
# /PLAY
# ============================================================

@bot.tree.command(
    name="play",
    description="Reproduce una canción o playlist."
)
@app_commands.describe(
    link="Link de YouTube, playlist o búsqueda"
)
async def play(
    interaction,
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

        # ====================================================
        # CONECTAR
        # ====================================================

        vc = await connect_to_voice(
            guild,
            channel
        )


        bot.join_channels[
            guild.id
        ] = channel.id


        # ====================================================
        # BUSCAR
        # ====================================================

        await interaction.followup.send(
            "🔎 Buscando..."
        )


        songs = await get_playlist_items(
            link
        )


        if not songs:

            await interaction.channel.send(
                "❌ No encontré canciones."
            )

            return


        # ====================================================
        # AGREGAR A COLA
        # ====================================================

        queue = bot.queues[
            guild.id
        ]


        was_empty = (
            not queue
            and not vc.is_playing()
            and not vc.is_paused()
        )


        for song in songs:

            queue.append(
                song
            )


        # ====================================================
        # RESPUESTA
        # ====================================================

        if len(songs) == 1:

            position = len(queue)

            await interaction.channel.send(
                f"➕ **Agregada a la cola**\n"
                f"🎵 {songs[0]['title']}\n"
                f"📋 Posición: **{position}**"
            )

        else:

            await interaction.channel.send(
                f"📃 **Playlist agregada**\n"
                f"🎵 Canciones: **{len(songs)}**\n"
                f"📋 Total en cola: **{len(queue)}**"
            )


        # ====================================================
        # COMENZAR SI ESTÁ VACÍO
        # ====================================================

        if was_empty:

            await play_next(
                guild.id
            )


    except Exception as e:

        log.exception(
            "❌ Error en /play"
        )


        try:

            await interaction.channel.send(
                f"❌ Error:\n```{e}```"
            )

        except Exception:
            pass


# ============================================================
# /QUEUE
# ============================================================

@bot.tree.command(
    name="queue",
    description="Muestra la cola."
)
async def queue(
    interaction
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


    current = bot.current_song.get(
        guild.id
    )


    text = ""


    if current:

        text += (
            "🎵 **Reproduciendo ahora:**\n"
            f"**{current['title']}**\n\n"
        )


    if not songs:

        if text:

            text += "📭 No hay canciones pendientes."

            await interaction.response.send_message(
                text
            )

        else:

            await interaction.response.send_message(
                "📭 La cola está vacía."
            )

        return


    text += "📋 **Siguiente:**\n\n"


    for i, song in enumerate(
        songs[:20],
        start=1
    ):

        text += (
            f"**{i}.** {song['title']}\n"
        )


    if len(songs) > 20:

        text += (
            f"\n📃 Y "
            f"**{len(songs) - 20}** "
            f"canciones más..."
        )


    await interaction.response.send_message(
        text
    )


# ============================================================
# /NOWPLAYING
# ============================================================

@bot.tree.command(
    name="nowplaying",
    description="Muestra la canción actual."
)
async def nowplaying(
    interaction
):

    guild = interaction.guild

    if guild is None:

        await interaction.response.send_message(
            "❌ Solo funciona en servidores."
        )

        return


    song = bot.current_song.get(
        guild.id
    )


    if not song:

        await interaction.response.send_message(
            "📭 No hay ninguna canción."
        )

        return


    await interaction.response.send_message(
        f"🎵 **Reproduciendo ahora**\n"
        f"**{song['title']}**\n"
        f"🔊 Volumen: 70%"
    )


# ============================================================
# /SKIP
# ============================================================

@bot.tree.command(
    name="skip",
    description="Salta la canción actual."
)
async def skip(
    interaction
):

    guild = interaction.guild

    if guild is None:

        return


    vc = guild.voice_client


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
# /PAUSE
# ============================================================

@bot.tree.command(
    name="pause",
    description="Pausa la música."
)
async def pause(
    interaction
):

    guild = interaction.guild

    vc = (
        guild.voice_client
        if guild
        else None
    )


    if not vc:

        await interaction.response.send_message(
            "❌ No estoy conectado."
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
    description="Reanuda la música."
)
async def resume(
    interaction
):

    guild = interaction.guild

    vc = (
        guild.voice_client
        if guild
        else None
    )


    if not vc:

        await interaction.response.send_message(
            "❌ No estoy conectado."
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
    description="Detiene la música y limpia la cola."
)
async def stop(
    interaction
):

    guild = interaction.guild

    if guild is None:

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

        if (
            vc.is_playing()
            or vc.is_paused()
        ):

            vc.stop()


    await interaction.response.send_message(
        "⏹️ Música detenida.\n"
        "🗑️ Cola limpiada.\n"
        "🔊 Sigo conectado."
    )


# ============================================================
# /CLEAR
# ============================================================

@bot.tree.command(
    name="clear",
    description="Limpia todas las canciones pendientes."
)
async def clear(
    interaction
):

    guild = interaction.guild

    if guild is None:

        return


    count = len(
        bot.queues[
            guild.id
        ]
    )


    bot.queues[
        guild.id
    ].clear()


    await interaction.response.send_message(
        f"🗑️ Cola limpiada.\n"
        f"🎵 Canciones eliminadas: **{count}**"
    )


# ============================================================
# /REMOVE
# ============================================================

@bot.tree.command(
    name="remove",
    description="Elimina una canción de la cola."
)
@app_commands.describe(
    position="Número de posición de la canción"
)
async def remove(
    interaction,
    position: int
):

    guild = interaction.guild

    if guild is None:

        return


    queue = bot.queues[
        guild.id
    ]


    if not queue:

        await interaction.response.send_message(
            "📭 La cola está vacía."
        )

        return


    if (
        position < 1
        or position > len(queue)
    ):

        await interaction.response.send_message(
            "❌ Esa posición no existe."
        )

        return


    song = queue.pop(
        position - 1
    )


    await interaction.response.send_message(
        f"🗑️ Eliminada de la cola:\n"
        f"**{song['title']}**"
    )


# ============================================================
# /SHUFFLE
# ============================================================

@bot.tree.command(
    name="shuffle",
    description="Mezcla la cola."
)
async def shuffle(
    interaction
):

    guild = interaction.guild

    if guild is None:

        return


    queue = bot.queues[
        guild.id
    ]


    if len(queue) < 2:

        await interaction.response.send_message(
            "❌ Necesitas al menos "
            "2 canciones en la cola."
        )

        return


    random.shuffle(
        queue
    )


    await interaction.response.send_message(
        "🔀 Cola mezclada."
    )


# ============================================================
# /LOOP
# ============================================================

@bot.tree.command(
    name="loop",
    description="Activa o desactiva repetir la canción."
)
async def loop(
    interaction
):

    guild = interaction.guild

    if guild is None:

        return


    bot.loop_mode[
        guild.id
    ] = not bot.loop_mode[
        guild.id
    ]


    if bot.loop_mode[
        guild.id
    ]:

        await interaction.response.send_message(
            "🔁 **Loop activado.**\n"
            "La canción actual se repetirá."
        )

    else:

        await interaction.response.send_message(
            "➡️ **Loop desactivado.**"
        )


# ============================================================
# /LEAVE
# ============================================================

@bot.tree.command(
    name="leave",
    description="Saca el bot del canal."
)
async def leave(
    interaction
):

    guild = interaction.guild

    if guild is None:

        return


    vc = guild.voice_client


    if not vc:

        await interaction.response.send_message(
            "❌ No estoy conectado."
        )

        return


    try:

        bot.queues[
            guild.id
        ].clear()


        bot.current_song.pop(
            guild.id,
            None
        )


        bot.loop_mode[
            guild.id
        ] = False


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
            "👋 Salí del canal.\n"
            "🗑️ Cola limpiada."
        )


    except Exception as e:

        await interaction.response.send_message(
            f"❌ Error saliendo:\n`{e}`"
        )


# ============================================================
# ERRORES DE SLASH COMMANDS
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

    print(
        "🚀 Iniciando Azulita..."
    )


    if not TOKEN:

        print(
            "❌ FALTA DISCORD_TOKEN"
        )

        raise RuntimeError(
            "Configura DISCORD_TOKEN en Railway."
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
