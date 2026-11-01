from codecs import BOM_UTF8, BOM_UTF16_LE, BOM_UTF32_BE
from datetime import date

import pytest

from contract_review.encoding import detect_encoding
from contract_review.presentation import Upload, process_uploads


TEXT = ('ДОГОВОР ПОСТАВКИ № TEST-789\r\nСтатус: подписан обеими сторонами 01.02.2030.\r\n\r\n'
        'Поставщик: ООО «Новый завод».\r\n\r\nОбщая стоимость договора составляет 123 456 рублей.\r\n\r\n'
        'Поставщик обязуется поставить товар не позднее 15.04.2030.')


@pytest.mark.parametrize('codec', ['utf-8', 'cp1251', 'koi8-r', 'cp866'])
def test_supported_document_encodings(codec):
    # DOS Cyrillic cannot encode typographic quotes: use the same ASCII quotes
    # in every encoding for this general test; real sample code pages follow.
    original = TEXT.replace('«', '"').replace('»', '"').replace('№ ', '')
    result = detect_encoding(original.encode(codec))
    assert result.encoding == codec
    assert result.text == original
    assert not result.manual


@pytest.mark.parametrize('codec,bom', [('utf-8', BOM_UTF8), ('utf-16-le', BOM_UTF16_LE), ('utf-32-be', BOM_UTF32_BE)])
def test_bom_is_recognized_and_retained(codec, bom):
    result = detect_encoding(bom + TEXT.encode(codec))
    assert result.encoding == codec
    assert result.text == '\ufeff' + TEXT
    output = process_uploads([Upload('bom.txt', bom + TEXT.encode(codec))], date(2030, 3, 10))
    assert output.report is not None
    output.extractions[0].validate_evidence()
    assert output.extractions[0].document.original_text == '\ufeff' + TEXT


def test_all_supplied_documents_are_detected_as_windows1251(examples):
    for extraction in examples.values():
        text = extraction.document.original_text
        result = detect_encoding(text.encode('cp1251'))
        assert result.encoding == 'cp1251'
        assert result.text == text


def test_mixed_encoding_batch_uses_each_files_own_codec(examples):
    uploads = [Upload(e.document.source_name, e.document.original_text.encode('utf-8' if i % 2 else 'cp1251'))
               for i, e in enumerate(examples.values())]
    output = process_uploads(uploads, date(2026, 3, 10))
    assert not output.errors and len(output.report.contracts) == 3
    for original, decoded in zip(examples.values(), output.extractions):
        assert original.document.original_text == decoded.document.original_text
        assert original.fields == decoded.fields
        decoded.validate_evidence()


@pytest.mark.parametrize('raw', [b'', b'\x00\x01\xff', b'\xff\xfe\x00', bytes(range(256)), b'\x89PNG\r\n\x1a\n'])
def test_unreadable_data_is_not_silently_replaced(raw):
    result = detect_encoding(raw)
    assert result.encoding is None and result.text is None
    assert result.message


def test_successful_legacy_decoding_alone_is_not_confidence():
    raw = 'Привет'.encode('cp1251')
    assert raw.decode('cp1251') == 'Привет'
    result = detect_encoding(raw)
    assert result.encoding is None
    assert result.alternatives
    assert any(c.encoding == 'cp1251' and c.preview == 'Привет' for c in result.alternatives)


def test_manual_fix_is_explicit_per_file_and_retains_other_files(examples):
    ambiguous = Upload('short.txt', 'Привет'.encode('cp1251'))
    main = Upload('base.txt', next(iter(examples.values())).document.original_text.encode())
    failed = process_uploads([main, ambiguous], date(2026, 3, 10))
    assert failed.report is None and failed.errors[0].upload_id == ambiguous.id
    repaired = process_uploads([main, ambiguous], date(2026, 3, 10), {ambiguous.id: 'cp1251'})
    assert not repaired.errors and repaired.report is not None
    assert repaired.extractions[1].document.original_text == 'Привет'
    assert repaired.report.unlinked_documents  # Decoding is not legal classification.
    assert detect_encoding(ambiguous.data, 'cp1251').manual


def test_manual_decoding_never_allows_controls_or_lossy_replacement():
    assert detect_encoding(b'\xff', 'utf-8').encoding is None
    assert detect_encoding(b'\x00\xff', 'cp1251').encoding is None
    assert detect_encoding(b'text', 'not-a-codec').encoding is None
    assert detect_encoding('Текст\ufffd'.encode()).encoding is None


def test_override_identity_distinguishes_same_filename_different_bytes():
    a = Upload('same.txt', 'Привет'.encode('cp1251'))
    b = Upload('same.txt', 'Привет'.encode('koi8-r'))
    assert a.id != b.id
    out = process_uploads([a, b], date(2030, 3, 10), {a.id: 'cp1251'})
    assert len(out.errors) == 1 and out.errors[0].upload_id == b.id
