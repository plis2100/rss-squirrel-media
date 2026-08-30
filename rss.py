import re
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright


WEB_URL = "https://squirrelmedia.es/noticias"
BASE_URL = "https://squirrelmedia.es"
ARCHIVO_RSS = Path("squirrel-media.xml")

MESES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def limpiar_texto(texto):
    return re.sub(r"\s+", " ", texto or "").strip()


def escapar_xml(texto):
    return (
        str(texto)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def es_enlace_noticia(url):
    ruta = urlparse(url).path.rstrip("/")

    if not ruta.startswith("/noticia/"):
        return False

    slug = ruta.removeprefix("/noticia/")

    return bool(slug) and "/" not in slug


def convertir_fecha(texto):
    texto = limpiar_texto(texto).lower()

    coincidencia = re.search(
        r"\b(enero|febrero|marzo|abril|mayo|junio|julio|"
        r"agosto|septiembre|setiembre|octubre|noviembre|diciembre)"
        r"\s*[-–—]\s*(\d{4})\b",
        texto,
    )

    if coincidencia:
        mes = MESES[coincidencia.group(1)]
        anio = int(coincidencia.group(2))

        return datetime(
            anio,
            mes,
            1,
            12,
            0,
            0,
            tzinfo=timezone.utc,
        )

    coincidencia = re.search(
        r"\b(\d{1,2})\s+de\s+"
        r"(enero|febrero|marzo|abril|mayo|junio|julio|"
        r"agosto|septiembre|setiembre|octubre|noviembre|diciembre)"
        r"\s+de\s+(\d{4})\b",
        texto,
    )

    if coincidencia:
        dia = int(coincidencia.group(1))
        mes = MESES[coincidencia.group(2)]
        anio = int(coincidencia.group(3))

        try:
            return datetime(
                anio,
                mes,
                dia,
                12,
                0,
                0,
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None

    return None


def obtener_lista_noticias(pagina):
    pagina.goto(
        WEB_URL,
        wait_until="domcontentloaded",
        timeout=90000,
    )

    pagina.wait_for_selector(
        'a[href*="/noticia/"]',
        timeout=60000,
    )

    pagina.wait_for_timeout(5000)

    resultados = pagina.locator(
        'a[href*="/noticia/"]'
    ).evaluate_all(
        """
        enlaces => enlaces.map(enlace => {
            let contenedor = enlace;

            for (let i = 0; i < 8; i++) {
                if (!contenedor.parentElement) {
                    break;
                }

                contenedor = contenedor.parentElement;

                const texto = (contenedor.innerText || "")
                    .replace(/\\s+/g, " ")
                    .trim();

                const patronFecha = /(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)\\s*[-–—]\\s*\\d{4}/i;

                if (patronFecha.test(texto)) {
                    break;
                }
            }

            const encabezado = contenedor.querySelector(
                "h1, h2, h3, h4, h5"
            );

            return {
                url: enlace.href,
                titulo: (
                    encabezado?.innerText ||
                    enlace.innerText ||
                    ""
                ).replace(/\\s+/g, " ").trim(),
                texto: (
                    contenedor.innerText || ""
                ).replace(/\\s+/g, " ").trim()
            };
        })
        """
    )

    noticias = []
    enlaces_vistos = set()

    for resultado in resultados:
        url = urljoin(
            BASE_URL,
            resultado.get("url", ""),
        )
        url = url.split("#")[0].split("?")[0].rstrip("/")

        if not es_enlace_noticia(url):
            continue

        if url in enlaces_vistos:
            continue

        titulo = limpiar_texto(
            resultado.get("titulo", "")
        )
        texto = limpiar_texto(
            resultado.get("texto", "")
        )

        if titulo.lower() in {
            "ver más",
            "ver mas",
            "leer más",
            "leer mas",
        }:
            continue

        if len(titulo) < 15:
            continue

        noticias.append(
            {
                "titulo": titulo,
                "url": url,
                "fecha": convertir_fecha(texto),
                "descripcion": "",
            }
        )

        enlaces_vistos.add(url)

    if not noticias:
        raise RuntimeError(
            "No se encontraron noticias de Squirrel Media"
        )

    return noticias[:20]


def completar_noticia(pagina, noticia, posicion):
    try:
        pagina.goto(
            noticia["url"],
            wait_until="domcontentloaded",
            timeout=90000,
        )

        pagina.wait_for_timeout(1000)

        titulo = limpiar_texto(
            pagina.locator("h1").first.inner_text(
                timeout=15000
            )
        )

        if titulo:
            noticia["titulo"] = titulo

        cuerpo = limpiar_texto(
            pagina.locator("body").inner_text(
                timeout=15000
            )
        )

        fecha = convertir_fecha(cuerpo)

        if fecha:
            noticia["fecha"] = fecha

        selectores = [
            "main article p",
            "article p",
            "main p",
        ]

        for selector in selectores:
            parrafos = pagina.locator(
                selector
            ).all_text_contents()

            parrafos_limpios = []

            for parrafo in parrafos:
                parrafo = limpiar_texto(parrafo)

                if len(parrafo) >= 40:
                    parrafos_limpios.append(parrafo)

            if parrafos_limpios:
                noticia["descripcion"] = " ".join(
                    parrafos_limpios
                )[:1200]
                break

    except Exception as error:
        print(
            f"No se pudo completar {noticia['url']}: {error}"
        )

    if noticia["fecha"] is None:
        noticia["fecha"] = datetime(
            2000,
            1,
            1,
            12,
            posicion % 60,
            tzinfo=timezone.utc,
        )

    return noticia


def obtener_noticias():
    with sync_playwright() as playwright:
        navegador = playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )

        contexto = navegador.new_context(
            locale="es-ES",

            # La web tiene un certificado SSL con fecha inválida.
            ignore_https_errors=True,

            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )

        pagina = contexto.new_page()

        noticias = obtener_lista_noticias(
            pagina
        )

        noticias_completas = []

        for posicion, noticia in enumerate(noticias):
            noticia_completa = completar_noticia(
                pagina,
                noticia,
                posicion,
            )

            noticias_completas.append(
                noticia_completa
            )

        navegador.close()

    noticias_completas.sort(
        key=lambda elemento: elemento["fecha"],
        reverse=True,
    )

    return noticias_completas


def crear_rss(noticias):
    ahora = datetime.now(timezone.utc)

    partes = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "<channel>",
        "<title>Squirrel Media - Noticias</title>",
        f"<link>{escapar_xml(WEB_URL)}</link>",
        (
            "<description>Últimas noticias oficiales "
            "de Squirrel Media</description>"
        ),
        "<language>es</language>",
        f"<lastBuildDate>{format_datetime(ahora)}</lastBuildDate>",
        "<ttl>60</ttl>",
    ]

    for noticia in noticias:
        partes.extend(
            [
                "<item>",
                f"<title>{escapar_xml(noticia['titulo'])}</title>",
                f"<link>{escapar_xml(noticia['url'])}</link>",
                (
                    f'<guid isPermaLink="true">'
                    f"{escapar_xml(noticia['url'])}</guid>"
                ),
                (
                    f"<pubDate>"
                    f"{format_datetime(noticia['fecha'])}"
                    f"</pubDate>"
                ),
                (
                    f"<description>"
                    f"{escapar_xml(noticia['descripcion'])}"
                    f"</description>"
                ),
                "</item>",
            ]
        )

    partes.extend(
        [
            "</channel>",
            "</rss>",
        ]
    )

    return "\n".join(partes)


def guardar_rss(contenido):
    archivo_temporal = ARCHIVO_RSS.with_suffix(
        ".xml.tmp"
    )

    archivo_temporal.write_text(
        contenido,
        encoding="utf-8",
    )

    archivo_temporal.replace(
        ARCHIVO_RSS
    )


def main():
    noticias = obtener_noticias()
    contenido = crear_rss(noticias)
    guardar_rss(contenido)

    print(
        f"RSS de Squirrel Media creada con "
        f"{len(noticias)} noticias"
    )

    for noticia in noticias[:5]:
        print(
            noticia["fecha"].strftime("%m/%Y"),
            "-",
            noticia["titulo"],
        )


if __name__ == "__main__":
    main()
