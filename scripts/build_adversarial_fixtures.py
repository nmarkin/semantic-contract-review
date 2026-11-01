"""Author the QA oracle BEFORE execution. No application imports or results.

All amounts, dates, applicability and sources below follow the written exercise.
Run once to materialize TXT inputs and the reviewable, frozen MATRIX.json.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1] / 'qa/adversarial'
DOCS = ROOT / 'documents'
CATALOG = {}
CASES = []
SUPPLIER = 'ООО «Учебные поставки»'


def document(key, text, signed=None, effective=None, created='2026-02-01'):
    CATALOG[key] = dict(file=f'documents/{key}.txt', signed=signed, effective=effective, created=created)
    (DOCS / f'{key}.txt').write_text(text, encoding='utf-8', newline='')
    return key


def money(value):
    return f'Общая стоимость договора составляет {value:,} рублей.'.replace(',', ' ')


def delivery(value):
    return f'Поставщик обязуется поставить товар не позднее {value}.'


def base(key='base', number='DEMO-900', amount=1000000, extra=''):
    return document(key, f'ДОГОВОР ПОСТАВКИ № {number}\nДата: 01.02.2026\n'
                    'Статус: подписан обеими сторонами 01.02.2026.\n\n'
                    f'Поставщик: {SUPPLIER}.\n\n' + (money(amount) if amount else '') +
                    '\n\n' + delivery('15.04.2026') + '\n\n' + extra, signed='2026-02-01')


def iso(raw):
    return '-'.join(reversed(raw.split('.')))


def amendment(key, ordinal, effective='05.02.2026', signed='04.02.2026', amount=None,
              deadline=None, extra='', supplier=SUPPLIER, status='подписано обеими сторонами', number='DEMO-900'):
    text = (f'ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ № {ordinal} К ДОГОВОРУ {number}\n'
            f'Дата: {signed}\nСтатус: {status} {signed}.\n\nПоставщик: {supplier}.\n\n')
    text += (money(amount) + '\n\n' if amount is not None else '')
    text += (delivery(deadline) + '\n\n' if deadline else '')
    text += extra + f'\n\nНастоящее соглашение вступает в силу {effective}.'
    return document(key, text, signed=iso(signed), effective=iso(effective), created=iso(signed))


def field(value, sources, history, state='resolved'):
    return dict(value=value, state=state, sources=list(sources), history=[list(step) for step in history])


def terms(amount=1000000, deadline='2026-04-15', amount_sources=('base',), deadline_sources=('base',),
          amount_history=(('base',),), deadline_history=(('base',),), amount_state='resolved', deadline_state='resolved'):
    return dict(total_amount=field(amount, amount_sources, amount_history, amount_state),
                delivery_deadline=field(deadline, deadline_sources, deadline_history, deadline_state))


def case(key, docs, expected, as_of='2026-03-11', proposals=(), reasons=(), rationale='', unlinked=()):
    applied = sorted({source for f in expected.values() for step in f['history'][1:] for source in step})
    CASES.append(dict(id=key, documents=[dict(key=d, name=f'{d}.txt') for d in docs], as_of=as_of,
                      contract='DEMO-900', expected=expected, applied=applied, proposals=list(proposals),
                      required_reasons=list(reasons), unlinked=list(unlinked), rationale=rationale,
                      required_evidence=sorted({s for f in expected.values() for s in f['sources']}),
                      proposal_policy='All uploaded proposals may be shown, but creation after as_of must be explicit; none is operative.'))


def main():
    DOCS.mkdir(parents=True, exist_ok=True)
    base()
    amendment('A', 1, amount=1100000, deadline='20.04.2026')
    document('B', 'ПИСЬМО ПОСТАВЩИКА\nДата: 12.02.2026\nОт: ' + SUPPLIER + '\nТема: договор DEMO-900\n\n'
             'Предлагаем перенести срок поставки с 20.04.2026 на 28.04.2026.\n\n'
             'Согласие покупателя не получено. Это предложение не согласовано.', created='2026-02-12')
    amendment('C', 2, effective='18.02.2026', signed='17.02.2026', deadline='25.04.2026')
    document('D', 'ПРОЕКТ ДОПОЛНИТЕЛЬНОГО СОГЛАШЕНИЯ № 30 К ДОГОВОРУ DEMO-900\n'
             'Дата проекта: 25.02.2026\nСтатус: не подписан ни одной стороной.\n\nПоставщик: ' + SUPPLIER + '.\n\n'
             'Предлагается изменить общую стоимость поставки на 1 300 000 рублей.\n\n'
             'Предлагается изменить срок поставки на 15.05.2026.', created='2026-02-25')
    amendment('E', 3, effective='05.03.2026', signed='04.03.2026', amount=1250000)
    amendment('F', 4, effective='10.03.2026', signed='09.03.2026', deadline='01.05.2026')
    amendment('G', 5, effective='10.03.2026', signed='08.03.2026', deadline='03.05.2026')
    amendment('H', 6, effective='20.03.2026', signed='19.03.2026', deadline='10.05.2026')
    # Hand-authored time slices: no application-generated expected values.
    slices = [
        ('2026-02-02', terms()),
        ('2026-02-06', terms(1100000, '2026-04-20', ('A',), ('A',), (('base',), ('A',)), (('base',), ('A',)))),
        ('2026-02-20', terms(1100000, '2026-04-25', ('A',), ('C',), (('base',), ('A',)), (('base',), ('A',), ('C',)))),
        ('2026-03-06', terms(1250000, '2026-04-25', ('E',), ('C',), (('base',), ('A',), ('E',)), (('base',), ('A',), ('C',)))),
        ('2026-03-11', terms(1250000, None, ('E',), (), (('base',), ('A',), ('E',)), (('base',), ('A',), ('C',)), deadline_state='requires_review')),
        ('2026-03-21', terms(1250000, '2026-05-10', ('E',), ('H',), (('base',), ('A',), ('E',)), (('base',), ('A',), ('C',), ('H',)))),
    ]
    for when, expected in slices:
        case('chain-' + when, ['base', *'ABCDEFGH'], expected, when, ('B', 'D'),
             ('same_date_conflict',) if when >= '2026-03-10' else (),
             'Signed and effective by as_of only; F/G conflict is deadline-only; H supersedes that conflict; B/D never apply.')

    amendment('late', 10, effective='05.02.2026', signed='20.02.2026', amount=1200000)
    for when in ('2026-02-10', '2026-02-19', '2026-02-20'):
        eligible = when == '2026-02-20'  # explicit independent boundary, not resolver output
        case('late-signature-' + when, ['base', 'late'],
             terms(1200000, amount_sources=('late',), amount_history=(('base',), ('late',))) if eligible else terms(),
             when, reasons=() if eligible else ('signed_after_as_of',),
             rationale='Backdated effective date alone is insufficient; both signature and effect must have occurred. No extra ban on retrospective clauses is invented.')
    amendment('future', 11, effective='20.03.2026', signed='02.02.2026', amount=1400000)
    case('future-effect', ['base', 'future'], terms(), reasons=('not_effective_yet',))
    case('exact-effective-day', ['base', 'future'], terms(1400000, amount_sources=('future',), amount_history=(('base',), ('future',))), '2026-03-20')
    amendment('same1', 12, amount=1100000)
    amendment('same2', 13, amount=1200000)
    amendment('equal', 14, amount=1100000)
    amendment('date_only', 15, deadline='20.04.2026')
    conflict = terms(None, amount_sources=(), amount_state='requires_review')
    case('same-date-different', ['base', 'same1', 'same2'], conflict, reasons=('same_date_conflict',))
    case('same-date-equal', ['base', 'same1', 'equal'], terms(1100000, amount_sources=('same1', 'equal'), amount_history=(('base',), ('same1', 'equal'))))
    case('independent-fields', ['base', 'same1', 'date_only'], terms(1100000, '2026-04-20', ('same1',), ('date_only',), (('base',), ('same1',)), (('base',), ('date_only',))))
    case('later-same-field', ['base', 'same1', 'same2', 'E'], terms(1250000, amount_sources=('E',), amount_history=(('base',), ('E',))), reasons=('same_date_conflict',))
    case('later-other-field', ['base', 'same1', 'same2', 'C'], terms(None, '2026-04-25', (), ('C',), (('base',),), (('base',), ('C',)), amount_state='requires_review'), reasons=('same_date_conflict',))
    amendment('mixed', 16, amount=1100000, extra='Предлагаем изменить срок поставки на 28.04.2026.')
    case('signed-plus-proposal', ['base', 'mixed'], terms(1100000, amount_sources=('mixed',), amount_history=(('base',), ('mixed',))), proposals=('mixed',), reasons=('non_operative_role',))
    amendment('many_proposals', 17, extra='Предлагаем изменить срок поставки с 15.04.2026 на 28.04.2026. Предлагаем изменить срок поставки с 15.04.2026 на 30.04.2026.')
    case('two-proposals-one-paragraph', ['base', 'many_proposals'], terms(), proposals=('many_proposals',), reasons=('non_operative_role',), rationale='Two independent date changes; one document card and one paragraph; neither is applied.')
    case('later-unsigned', ['base', 'A', 'D'], slices[1][1], proposals=('D',))
    case('letter-wrong-old-date', ['base', 'C', 'B'], terms(deadline='2026-04-25', deadline_sources=('C',), deadline_history=(('base',), ('C',))), proposals=('B',), rationale='Letter references 20 April but current is 25 April: delta to 28 April must be +3, not +8.')
    amendment('contradictory_signing', 18, amount=1700000, extra='Статус: не подписано обеими сторонами.')
    case('contradictory-signing', ['base', 'contradictory_signing'], conflict, reasons=('unconfirmed_applicability',))
    base('second_base', amount=990000)
    both_review = terms(None, None, (), (), (), (), 'requires_review', 'requires_review')
    case('two-bases', ['base', 'second_base'], both_review, reasons=('multiple_bases',))
    amendment('wrong_supplier', 19, amount=1100000, supplier='АО «Иная сторона»')
    case('supplier-conflict', ['base', 'wrong_supplier'], conflict, reasons=('supplier_conflict',))
    base('other_base', number='DEMO-901')
    amendment('two_links', 20, amount=1100000, extra='Изменение относится также к договору DEMO-901.')
    case('two-contract-links', ['base', 'other_base', 'two_links'], conflict, reasons=('ambiguous_contract_reference',), unlinked=('two_links',))
    case('missing-base', ['same1'], both_review, reasons=('missing_base',))
    case('duplicate-content', ['base', 'A', 'A'], slices[1][1])
    case('same-filename', ['base', 'same1', 'same2'], conflict, reasons=('same_date_conflict',))
    CASES[-1]['documents'] = [dict(key=d, name='identical-name.txt') for d in ('base', 'same1', 'same2')]

    # Safety expectation: no unsupported arithmetic/date inference; flag the affected field.
    for key, sentence, affected in (
        ('unknown-dispatch', 'Стороны согласовали новую дату исполнения обязательств по отгрузке: 09.05.2030.', 'delivery'),
        ('unknown-completion', 'Дата завершения поставки изменяется на 09.05.2030.', 'delivery'),
        ('price-increment', 'Стороны согласовали увеличить цену договора на 200 000 рублей.', 'amount'),
        ('relative-deadline', 'Срок поставки: в течение 30 дней после получения аванса.', 'delivery'),
        ('conflicting-amounts', money(1100000) + '\n\n' + money(1200000), 'amount'),
    ):
        amendment(key, 21, extra=sentence)
        expected = conflict if affected == 'amount' else terms(deadline=None, deadline_sources=(), deadline_state='requires_review')
        case(key, ['base', key], expected, rationale='Explicit potential amendment must not leave old field confidently resolved; no inferred new numeric value is required.')
    base('missing_spec', amount=None, extra='Общая стоимость договора определяется спецификацией. Приложение № 1 не входит в предоставленный комплект.\n\nБюджетный лимит: 2 000 000 рублей.')
    case('missing-spec-budget', ['missing_spec'], terms(None, amount_sources=(), deadline_sources=('missing_spec',), amount_history=(), deadline_history=(('missing_spec',),), amount_state='missing'))

    # Every generated case gets a full written oracle BEFORE tests execute.
    rng = random.Random(42026)
    templates = [c for c in CASES if c['id'] in (
        'same-date-different', 'same-date-equal', 'independent-fields', 'later-same-field',
        'later-other-field', 'future-effect', 'exact-effective-day', 'late-signature-2026-02-19')]
    for i in range(160):
        template = templates[i % len(templates)]
        generated = deepcopy(template)
        generated['id'] = f'meta-{i:03d}-{template["id"]}'
        generated['metamorphic'] = dict(seed=42026, template=template['id'])
        # Joint perturbations: renamed files, shuffled order, duplicates, neutral draft.
        generated['documents'].append(dict(key='D', name='signed-final-contract.txt'))
        generated['proposals'] = ['D']
        generated['documents'].append(deepcopy(rng.choice(generated['documents'])))
        for j, item in enumerate(generated['documents']):
            item['name'] = f'{rng.randrange(100000):05d}-переименовано-{j}.txt'
        rng.shuffle(generated['documents'])
        generated['rationale'] += ' Order/name/duplicate/draft transformations cannot change this independent oracle.'
        CASES.append(generated)

    payload = dict(oracle='Authored from exercise rules, never generated from Resolver output.', seed=42026,
                   documents=CATALOG, cases=CASES,
                   ui_expectations=dict(zero='Collapsed neutral expander; exact empty message after opening.',
                       future_proposals='Identify creation after as_of explicitly in card/nearby visible status.',
                       superseded_conflict='Retained conflict cards must identify that the conflict is historical/resolved by a later amendment.',
                       many_proposals='Two change lines in one document card; shared source paragraph once.'))
    content = json.dumps(payload, ensure_ascii=False, indent=2) + '\n'
    (ROOT / 'MATRIX.json').write_text(content, encoding='utf-8')
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(DOCS.glob('*.txt'))}
    hashes['MATRIX.json'] = hashlib.sha256((ROOT / 'MATRIX.json').read_bytes()).hexdigest()
    (ROOT / 'INPUT_SHA256.json').write_text(json.dumps(hashes, indent=2) + '\n', encoding='utf-8')
    print(f'Frozen before execution: {len(CASES)} cases, {len(CATALOG)} TXT documents, seed=42026')


if __name__ == '__main__':
    main()
