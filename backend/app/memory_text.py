"""ASCII pagination matching pinned LVGL 9.3 text layout, Montserrat 24.

The word/line rules below are adapted from LVGL's MIT-licensed lv_text.c.
Glyph widths include native kerning/rounding measured by the host probe.
No runtime compiler, font engine, library download or external service is used.
"""
from .memory_font_metrics import GLYPH_WIDTHS

WIDTH = 620
HEIGHT = 76
LETTER_SPACE = 2  # make_label() applies this to Friday's subtitle.
LINE_HEIGHT = 27
LINE_SPACE = 0  # Ordinary label default; theme's large spacing is for textareas.
BREAK_CHARS = ' ,.;:-_)}'  # Pinned Friday sdkconfig; long-word length setting is zero.
LAYOUT_VERSION = 'lvgl93-montserrat24-620x76-letter2-explicitrows-v3'


def glyph_width(character, following=''):
    return GLYPH_WIDTHS[(ord(character)-32)*96 + (ord(following)-31 if following else 0)]


def _word(text, budget, break_all):
    """Return the next native word advance and width (including trailing spacing)."""
    width = 0
    accepted_width = 0
    first_overflow = None
    index = 0
    while index < len(text):
        character = text[index]
        following = text[index+1] if index+1 < len(text) else ''
        advance = glyph_width(character, following)
        width += advance + (LETTER_SPACE if advance else 0)
        if first_overflow is None and width-LETTER_SPACE > budget:
            first_overflow = index
            if break_all:
                break
        if character in BREAK_CHARS:
            if index == 0 and first_overflow is None:
                accepted_width = width
            # A break character terminates its own one-character word.
            if first_overflow is None:
                return (1 if index == 0 else index), accepted_width
            break
        if first_overflow is None:
            accepted_width = width
        index += 1
    if first_overflow is None:
        return index, accepted_width
    # LV_TXT_LINE_BREAK_LONG_LEN=0: a later word moves intact to the next line.
    return (first_overflow, accepted_width) if break_all else (0, 0)


def next_line(text):
    index = 0
    budget = WIDTH
    while index < len(text) and budget > 0:
        advance, width = _word(text[index:], budget, index == 0)
        budget -= width
        if not advance:
            break
        index += advance
    return max(index, 1) if text else 0


def line_ends(text):
    ends = []
    offset = 0
    while offset < len(text):
        offset += next_line(text[offset:])
        ends.append(offset)
    return ends


def line_width(text):
    return sum(glyph_width(character,text[index+1] if index+1<len(text) else '')+LETTER_SPACE
               for index,character in enumerate(text))-LETTER_SPACE if text else 0


def _review_lines(text):
    """Measure at most two rows, inserting only a presentation line boundary."""
    rows=[]
    offset=0
    while offset<len(text) and len(rows)<2:
        remaining=text[offset:]
        low,high=1,len(remaining)
        while low<high:
            middle=(low+high+1)//2
            if line_width(remaining[:middle])<=WIDTH:low=middle
            else:high=middle-1
        end=low
        if end<len(remaining):
            boundary=max((index+1 for index,character in enumerate(remaining[:end])
                          if character in BREAK_CHARS),default=0)
            if boundary>=end*0.9:end=boundary
        rows.append(remaining[:end])
        offset+=end
    return rows


def text_pages(text):
    """Source fragments: concatenate these to recover every original character."""
    if not text:return ['']
    if any(not 32<=ord(character)<=126 for character in text):
        raise ValueError('Use printable ASCII text for native review')
    pages=[]
    offset=0
    while offset<len(text):
        page=''.join(_review_lines(text[offset:]))
        pages.append(page)
        offset+=len(page)
    return pages


def display_page(page):
    """Two exact font-measured display rows; the newline is never stored as fact data."""
    rows=_review_lines(page)
    if ''.join(rows)!=page:raise ValueError('Memory page exceeds native review geometry')
    return '\n'.join(rows)
