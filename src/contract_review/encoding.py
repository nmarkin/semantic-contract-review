"""Conservative per-file TXT decoding, without lossy replacement or rewriting."""

from codecs import BOM_UTF8, BOM_UTF16_BE, BOM_UTF16_LE, BOM_UTF32_BE, BOM_UTF32_LE
from dataclasses import dataclass
import re
import unicodedata


ENCODING_LABELS = {
    'utf-8': 'UTF-8', 'cp1251': 'Windows-1251', 'koi8-r': 'KOI8-R',
    'cp866': 'CP866', 'utf-16-le': 'UTF-16 LE', 'utf-16-be': 'UTF-16 BE',
    'utf-32-le': 'UTF-32 LE', 'utf-32-be': 'UTF-32 BE',
}
LEGACY_ENCODINGS = ('cp1251', 'koi8-r', 'cp866')
# These are language/domain signals, not known document values or filenames.
DOCUMENT_WORDS = re.compile(
    r'\b(?:договор\w*|постав\w*|подпис\w*|стоимост\w*|соглашен\w*|сторон\w*|'
    r'рубл\w*|проект\w*|приложен\w*|предлага\w*|оборудован\w*|срок\w*|покупател\w*|дата)\b',
    re.IGNORECASE,
)
COMMON_PAIRS = frozenset(('ст', 'но', 'ен', 'то', 'на', 'ов', 'ни', 'ра', 'во', 'ко',
                          'ро', 'по', 'ос', 'пр', 'ре', 'го', 'ал', 'до', 'ли', 'ер',
                          'от', 'ть', 'ан', 'ет', 'ор', 'та', 'те', 'ла', 'ол', 'ва'))


@dataclass(frozen=True)
class EncodingCandidate:
    encoding: str
    preview: str
    score: float


@dataclass(frozen=True)
class EncodingResult:
    encoding: str | None
    text: str | None
    message: str = ''
    alternatives: tuple[EncodingCandidate, ...] = ()
    manual: bool = False


def _text_problem(text: str) -> str | None:
    if not text.lstrip('\ufeff').strip():
        return 'Файл пуст. Загрузите TXT с текстом документа.'
    if '\ufffd' in text:
        return 'В тексте уже есть поврежденные символы. Сохраните документ заново из оригинала.'
    if any(unicodedata.category(c) in ('Cc', 'Cs') and c not in '\t\r\n' for c in text):
        return 'Файл содержит нетекстовые символы. Сохраните оригинал как обычный TXT.'
    return None


def _language_score(text: str) -> tuple[float, bool]:
    letters = [c for c in text if c.isalpha()]
    russian = sum(c.lower() in 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя' for c in letters)
    fraction = russian / max(len(letters), 1)
    words = re.findall(r'[а-яё]+', text.lower())
    pairs = [word[i:i + 2] for word in words for i in range(len(word) - 1)]
    common = sum(pair in COMMON_PAIRS for pair in pairs) / max(len(pairs), 1)
    anchors = len({m.group().lower() for m in DOCUMENT_WORDS.finditer(text)})
    # Box-drawing, symbols and non-Russian alphabet characters often indicate
    # a wrong Cyrillic code page. Punctuation, numbers and whitespace are fine.
    noise = sum((unicodedata.category(c).startswith('S') and c not in '№₽%')
                or ('\u2500' <= c <= '\u259f') for c in text) / max(len(text), 1)
    score = 4 * min(anchors, 12) + 10 * common + 2 * fraction - 30 * noise
    plausible = len(letters) >= 20 and fraction >= .70 and anchors >= 2 and common >= .08 and noise < .03
    return score, plausible


def detect_encoding(data: bytes, override: str | None = None) -> EncodingResult:
    """BOM/strict UTF-8 first, otherwise require a strong Cyrillic-language lead.

    A successful legacy decode is only a candidate. Close/weak scores require
    a human choice. Explicit choices still reject malformed bytes and controls.
    Endian-specific BOM codecs retain U+FEFF in the resulting original string.
    """
    if override is not None:
        if override not in ENCODING_LABELS:
            return EncodingResult(None, None, 'Эта кодировка не поддерживается.')
        try:
            text = data.decode(override, errors='strict')
        except UnicodeDecodeError:
            return EncodingResult(None, None, 'Не удалось прочитать файл в выбранной кодировке. Выберите другую или сохраните TXT в UTF-8.')
        problem = _text_problem(text)
        return EncodingResult(None, None, problem) if problem else EncodingResult(override, text, manual=True)

    for bom, codec in ((BOM_UTF32_LE, 'utf-32-le'), (BOM_UTF32_BE, 'utf-32-be'),
                       (BOM_UTF8, 'utf-8'), (BOM_UTF16_LE, 'utf-16-le'), (BOM_UTF16_BE, 'utf-16-be')):
        if data.startswith(bom):
            try:
                text = data.decode(codec, errors='strict')
            except UnicodeDecodeError:
                return EncodingResult(None, None, 'Файл с меткой кодировки поврежден или обрезан. Загрузите его заново из оригинала.')
            problem = _text_problem(text)
            return EncodingResult(None, None, problem) if problem else EncodingResult(codec, text)
    try:
        text = data.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        pass
    else:
        problem = _text_problem(text)
        return EncodingResult(None, None, problem) if problem else EncodingResult('utf-8', text)

    decoded = []
    for codec in LEGACY_ENCODINGS:
        try:
            text = data.decode(codec, errors='strict')
        except UnicodeDecodeError:
            continue
        if _text_problem(text):
            continue
        score, plausible = _language_score(text)
        decoded.append((EncodingCandidate(codec, text[:500], score), text, plausible))
    decoded.sort(key=lambda item: (-item[0].score, item[0].encoding))
    alternatives = tuple(item[0] for item in decoded)
    if decoded:
        best, text, plausible = decoded[0]
        margin = best.score - decoded[1][0].score if len(decoded) > 1 else float('inf')
        if plausible and margin >= 3:
            return EncodingResult(best.encoding, text)
        return EncodingResult(None, None, 'Не удалось однозначно прочитать текст. Выберите кодировку этого файла и проверьте предпросмотр.', alternatives)
    return EncodingResult(None, None, 'Не удалось прочитать TXT без повреждения символов. Сохраните оригинал в UTF-8 и загрузите снова.')
