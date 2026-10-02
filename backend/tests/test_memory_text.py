import random
import string
import unittest
from app.memory_text import text_pages, line_ends, glyph_width, WIDTH, HEIGHT, LINE_HEIGHT, LINE_SPACE,display_page,line_width


class MemoryTextTests(unittest.TestCase):
    def assert_fits(self,text):
        pages=text_pages(text)
        self.assertEqual(''.join(pages),text)
        for page in pages:
            display=display_page(page)
            rows=display.split('\n')
            self.assertEqual(''.join(rows),page)
            self.assertLessEqual(len(rows),2)
            self.assertLessEqual(len(rows)*LINE_HEIGHT+max(0,len(rows)-1)*LINE_SPACE,HEIGHT)
            for row in rows:self.assertLessEqual(line_width(row),WIDTH)
        return pages

    def test_width_wordwrap_punctuation_and_exact_boundaries(self):
        for text in ['W'*500,'i'*500,('W'*11+' ')*41,('W'*22+' ')*21,('one, two; three: four-five! ')*18,('()[]{}<>.,;:_-!@#$%^&*  ')*20,'a'+' '*498+'z',' '*250+'W'*250,'I prefer green tea',('W'*21+' '*2+'W'*21), ''.join(chr(i) for i in range(32,127))*5]:
            with self.subTest(text=text[:30]):self.assert_fits(text)

    def test_common_and_wide_page_counts(self):
        common=('I like green tea without sugar and prefer a quiet workspace when I read. Please keep replies concise!')[:100]
        self.assertEqual(len(self.assert_fits(common)),2)
        self.assertEqual(len(self.assert_fits(((common+' ')*5)[:500])),6)
        self.assertEqual(len(self.assert_fits('W'*500)),12)
        self.assertLessEqual(len(self.assert_fits(((('W'*11+' ')*60)[:500]))),13)
        self.assertEqual(len(self.assert_fits('I prefer green tea')),1)

    def test_seeded_ascii_and_no_character_loss(self):
        rng=random.Random(71)
        for _ in range(200):
            text=''.join(rng.choices(' '+string.ascii_letters+string.digits+string.punctuation,k=rng.randint(1,500)))
            self.assert_fits(text)

    def test_unsupported_characters_and_font_rounding(self):
        for text in ['café','😀','new\nline','\ttext']:
            with self.assertRaises(ValueError):text_pages(text)
        self.assertEqual(text_pages(''),[''])
        self.assertEqual(glyph_width('W','W'),27)
        self.assertEqual(glyph_width('W'),27)
        self.assertEqual(line_ends('W'*100),[21,42,63,84,100])
