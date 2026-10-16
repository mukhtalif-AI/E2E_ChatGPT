import unittest

from engines.voice.voice.textsplit import sentences
from engines.voice.voice.speak import chunk, dramatic, flow_groups


def words(text):
    import re
    return re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE)


CASES = [
    ("dependent clauses", "जब बारिश थमी और गाँव की पगडंडी पर पानी चमकने लगा, तब मीरा ने वह पुराना दरवाज़ा खोला जिसके पीछे उसकी माँ की आख़िरी चिट्ठी रखी थी।"),
    ("पर location", "खिड़की पर रखा दीपक हवा से काँप रहा था।"),
    ("पर contrast", "मैंने उसे पुकारा, पर उसने पीछे मुड़कर नहीं देखा।"),
    ("context words", "उसने कहा कि अगर तुम फिर आओ तो मैं भी यहीं रहूँगा। वह ही इस बात को सच मानती थी।"),
    ("suspense reveal", "दरवाज़ा धीरे से खुला। सामने कोई नहीं था।"),
    ("emotional dialogue", "उसने काँपती आवाज़ में कहा, “मैंने तुम्हारा इंतज़ार किया।”"),
    ("short complete sentence", "वह रुक गया। बस। फिर चल पड़ा।"),
    ("long comma pause", "रात गहरी थी, रास्ता सुनसान था, दूर कहीं कुत्ते भौंक रहे थे, और उसके हाथ में छोटी-सी लालटेन काँप रही थी।"),
    ("connected paragraph", "वह रोज़ उसी पेड़ के नीचे बैठता था। वहाँ से नदी का मोड़ साफ़ दिखाई देता था। आज पानी कुछ ज़्यादा शांत था।"),
    ("Sukoon calm", "धीरे-धीरे रात शांत हुई। खिड़की से आती हवा ने परदे को हल्के से छुआ। उसने लंबी साँस ली और आराम से आँखें बंद कर लीं।"),
]


class HindiNarrationRegressionTests(unittest.TestCase):
    def test_representative_text_keeps_every_spoken_word_in_order(self):
        for label, text in CASES:
            with self.subTest(case=label):
                pieces = sentences(text)
                self.assertEqual(words(text), words(" ".join(pieces)))

    def test_short_terminal_sentences_are_not_merged(self):
        self.assertEqual(["वह रुक गया।", "बस।", "फिर चल पड़ा."], sentences("वह रुक गया। बस। फिर चल पड़ा."))

    def test_location_and_contrast_are_not_split_at_postposition_or_conjunction(self):
        location = "खिड़की पर रखा दीपक हवा से काँप रहा था।"
        contrast = "मैंने उसे पुकारा, पर उसने पीछे मुड़कर नहीं देखा।"
        self.assertEqual([location], sentences(location))
        self.assertEqual([contrast], sentences(contrast))

    def test_long_unpunctuated_fallback_preserves_tokens(self):
        text = "जब वह पुराने घर की ओर लौट रहा था तो उसे लगा कि कोई उसके पीछे चल रहा है और फिर भी उसने मुड़कर नहीं देखा क्योंकि उसे अपने डर का सच जानना था"
        parts = chunk(text, max_words=10)
        self.assertGreater(len(parts), 1)
        self.assertEqual(words(text), words(" ".join(parts)))

    def test_dramatic_mode_does_not_infer_pauses_from_lead_words_or_short_reveals(self):
        units = [
            {"text": "वह लौटा।", "pause": 0.3},
            {"text": "फिर उसने दरवाज़ा खोला।", "pause": 0.3},
            {"text": "कोई नहीं।", "pause": 0.3},
        ]
        result = dramatic(units, {"pause_line": 1.0, "pause_tension": 1.7, "pause_tension_max": 2.6})
        self.assertEqual([0.3, 0.3, 0.0], [x["pause"] for x in result])
        self.assertEqual([x["text"] for x in units], [x["text"] for x in result])

    def test_explicit_ellipsis_is_a_held_pause(self):
        result = dramatic([{"text": "वहाँ...", "pause": 0.4}, {"text": "फिर सन्नाटा छा गया।", "pause": 0.3}],
                          {"pause_line": 1.0, "pause_tension": 1.7, "pause_tension_max": 2.6})
        self.assertTrue(result[0]["hold"])
        self.assertGreaterEqual(result[0]["pause"], 1.7)

    def test_flow_join_preserves_punctuation_and_does_not_invent_any(self):
        plan = [
            {"text": "वह खिड़की के पास बैठा", "pause": 0.2},
            {"text": "और चुपचाप बारिश सुनता रहा", "pause": 0.2},
        ]
        merged = flow_groups(plan, {"flow_max_words": 28, "pause_marker": 1.6})
        self.assertEqual("वह खिड़की के पास बैठा और चुपचाप बारिश सुनता रहा", merged[0]["text"])
        self.assertEqual(words(" ".join(p["text"] for p in plan)), words(merged[0]["text"]))


if __name__ == "__main__":
    unittest.main()
