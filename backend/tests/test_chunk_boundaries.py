from app.retrieval import chunks_for, readable_evidence, MAX_EVIDENCE_CHARS
from test_qa import document


def test_completion_preserves_ids_order_and_original_evidence():
    doc = document()
    sentence = 'It is challenging to make efficient verification and ensure logical consistency.'
    doc.sections[0].fragments[0].text = 'Earlier experimental observations are discussed. ' * 20 + sentence
    raw = chunks_for(doc)
    expanded = readable_evidence(doc, raw)
    assert [c['id'] for c in raw] == [c['id'] for c in expanded]
    assert any(sentence in c['text'] for c in expanded)
    assert all(c['text'].strip() in e['text'] for c, e in zip(raw, expanded))
    assert all(len(c['text']) <= MAX_EVIDENCE_CHARS for c in expanded)
    assert expanded[-1]['page'] == 2
    assert expanded[-1]['text'] == raw[-1]['text']


def test_no_cross_page_and_long_sentence_stays_bounded():
    doc = document()
    doc.sections[0].fragments[0].text = 'word ' * 1500
    expanded = readable_evidence(doc, chunks_for(doc))
    assert all(len(c['text']) <= MAX_EVIDENCE_CHARS for c in expanded)
    assert all('Limitations' not in c['text'] for c in expanded if c['page'] == 1)
    assert all(c['text'].endswith('word') for c in expanded if c['page'] == 1)
    assert readable_evidence(doc, []) == []


def test_chinese_and_unbroken_fallback_preserve_source():
    doc = document()
    for text in ['完整句子。' * 500, '字' * 2100]:
        doc.sections[0].fragments[0].text = text
        expanded = readable_evidence(doc, chunks_for(doc))
        assert all(c['text'] in text and len(c['text']) <= MAX_EVIDENCE_CHARS
                   for c in expanded if c['page'] == 1)
