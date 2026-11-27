#!/usr/bin/env python3
"""Check recorded invariants, not clinical truth. Standard library only; offline."""
import argparse
from datetime import datetime, time
import hashlib
from html import escape
from itertools import combinations
import json
from pathlib import Path
import re
from urllib.parse import quote


BEGIN = '<!-- clinical-ledger:start -->'
END = '<!-- clinical-ledger:end -->'


def pair_state(pair, claims):
    """Display state is derived from the linked claims, never entered twice."""
    linked = [claims[i] for i in pair.get('claim_ids', []) if i in claims]
    if any(c.get('state') == 'verified' for c in linked):
        return 'verified'
    if linked:
        return 'clue'
    return pair.get('search_result', 'unreadable')


def validate(data):
    errors, warnings = [], []
    def require(ok, message):
        if not ok:
            errors.append(message)
    def index(rows, label):
        ids = [r.get('id') for r in rows]
        require(all(isinstance(i, str) and i for i in ids), label + ': missing id')
        require(len(ids) == len(set(ids)), label + ': duplicate id')
        return {r.get('id'): r for r in rows}
    def moment(value, label):
        try:
            if data.get('time_basis', 'datetime') == 'clock':
                if not isinstance(value, str) or not re.fullmatch(r'\d{2}:\d{2}(?::\d{2})?', value):
                    raise ValueError('expected HH:MM[:SS]')
                return time.fromisoformat(value)
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            errors.append(label + ': invalid time for declared time_basis')
            return None

    if data.get('schema_version') != 2:
        return {'ok': False, 'errors': ['schema_version must be 2; rebuild claim-level bindings from actual sources, do not silently upgrade a v1 ledger'], 'warnings': []}
    require(data.get('time_basis', 'datetime') in ('datetime', 'clock'), 'invalid time_basis')
    if data.get('time_basis') == 'clock':
        require(bool(data.get('time_basis_note')), 'clock requires a same-episode/ordering basis; do not invent a date or cross-midnight order')
    mode, cutoff = data.get('time_mode'), data.get('cutoff')
    require(mode in ('current', 'historical'), 'invalid time_mode')
    t = None
    if mode == 'current':
        require(cutoff is None, 'current mode must not invent a cutoff')
    elif mode == 'historical':
        require(isinstance(cutoff, dict), 'historical cutoff needs an explicit basis')
        if isinstance(cutoff, dict):
            require(cutoff.get('source') in ('user', 'selected_task'), 'cutoff source must be explicit')
            require(bool(cutoff.get('quote')), 'cutoff needs the actual request/task excerpt')
            t = moment(cutoff.get('value'), 'cutoff')

    materials = index(data.get('materials', []), 'materials')
    facts = index(data.get('facts', []), 'facts')
    keys = []
    missing = []
    for fid, f in facts.items():
        key = (f.get('object'), f.get('field'))
        require(all(isinstance(k, str) and k for k in key), str(fid) + ': object/field required')
        keys.append(key)
        state, value = f.get('state'), f.get('value')
        require(state in ('provided', 'missing', 'conflict', 'unreadable'), str(fid) + ': invalid state')
        quotes = f.get('quotes', [])
        for q in quotes:
            raw = materials.get(q.get('material'), {}).get('text', '')
            require(bool(q.get('text')) and q.get('text') in raw, str(fid) + ': quote not present in material')
        if state == 'provided':
            require(isinstance(value, str) and bool(value), str(fid) + ': provided value must be raw text')
            require(bool(quotes), str(fid) + ': provided value needs a material quote')
            require(isinstance(value, str) and any(value in q.get('text', '') for q in quotes), str(fid) + ': value absent from quoted input')
        else:
            missing.append(fid)
            require(value is None, str(fid) + ': unknown/conflicting field must not carry one asserted value')
            if state == 'conflict':
                require(len(quotes) >= 2, str(fid) + ': conflict needs both source excerpts')
    require(len(keys) == len(set(keys)), 'facts: duplicate object/field, including provided-vs-missing collision')

    events = index(data.get('events', []), 'events')
    for eid, event in events.items():
        section, at = event.get('section'), event.get('at')
        ids = event.get('fact_ids', [])
        require(bool(ids) and all(i in facts for i in ids), str(eid) + ': missing fact references')
        require(event.get('visibility') in ('known', 'unknown', 'not_applicable'), str(eid) + ': explicit visibility state required')
        if at is None:
            require(section == 'unknown', str(eid) + ': unknown time needs unknown section, no invented bound')
            continue
        value = moment(at, str(eid))
        if mode == 'current':
            require(section == 'current', str(eid) + ': current mode cannot create a post-cutoff section')
        elif t and value:
            try:
                expected = 'before' if value <= t else 'after'
                require(section == expected, str(eid) + ': event is in wrong cutoff section')
            except TypeError:
                errors.append(str(eid) + ': mixed timezone formats')
        require(event.get('visibility') in ('known', 'unknown', 'not_applicable'), str(eid) + ': explicit visibility state required')

    sources = index(data.get('sources', []), 'sources')
    placeholder = re.compile(r'见原始链接|见会话|详见会话|待补链接|placeholder', re.I)
    for sid, s in sources.items():
        require(bool(s.get('title')), str(sid) + ': title required')
        level = s.get('read_level')
        require(level in ('body', 'excerpt', 'abstract', 'generated', 'secondary', 'unreadable'), str(sid) + ': invalid reading level')
        locator, kind = s.get('locator'), s.get('locator_type')
        require(kind in ('url', 'doi', 'pmid', 'document', 'internal', 'missing'), str(sid) + ': invalid locator_type')
        if kind == 'missing':
            require(locator is None, str(sid) + ': missing locator must be null')
            require(bool(s.get('limitation')), str(sid) + ': explain missing traceability')
            warnings.append(str(sid) + ': no unique locator, only a lead')
        else:
            require(isinstance(locator, str) and bool(locator) and not placeholder.search(locator), str(sid) + ': real locator required, not a placeholder')
            if kind == 'url':
                require(isinstance(locator, str) and bool(re.match(r'^https?://[^\s<>]+$', locator)), str(sid) + ': invalid URL')
            if kind == 'doi':
                require(isinstance(locator, str) and bool(re.match(r'^10\.\d{4,9}/\S+$', locator)), str(sid) + ': invalid DOI')
            if kind == 'pmid':
                require(isinstance(locator, str) and locator.isdigit(), str(sid) + ': invalid PMID')
            if kind == 'internal':
                require(bool(s.get('limitation')), str(sid) + ': disclose original-workbench access limit')
        require(bool(s.get('tool_ref')) or bool(s.get('tool_ref_note')), str(sid) + ': preserve tool reference or state why absent')
        require('verified_claims' not in s, str(sid) + ': use claims/supports, not a duplicate source-level verified_claims list')

    claims = index(data.get('claims', []), 'claims')
    for cid, c in claims.items():
        state = c.get('state')
        require(state in ('verified', 'clue', 'unreadable'), cid + ': invalid claim state')
        require(isinstance(c.get('text'), str) and bool(c['text'].strip()), cid + ': claim text required')
        require('conditions' in c and isinstance(c['conditions'], str), cid + ': state applicable conditions or explicit limits')
        supports = c.get('supports', [])
        require('supports' in c and isinstance(supports, list), cid + ': supports must be an explicit list')
        direct = False
        for support in supports:
            sid = support.get('source')
            source = sources.get(sid, {})
            require(sid in sources, cid + ': unknown support source')
            relation = support.get('basis')
            require(relation in ('direct', 'secondary'), cid + ': support basis must be direct or secondary')
            excerpt = support.get('quote')
            raw = source.get('read_text', '')
            matched = isinstance(excerpt, str) and bool(excerpt.strip()) and isinstance(raw, str) and excerpt in raw
            require(matched, cid + ': support quote absent from recorded source text')
            require(bool(support.get('location')), cid + ': support read position required')
            eligible = matched and relation == 'direct' and source.get('read_level') in ('body', 'excerpt') and source.get('locator_type') not in (None, 'missing')
            direct = direct or eligible
        if state == 'verified':
            require(direct, cid + ': verified claim needs direct readable evidence; a secondary quote cannot certify the underlying claim')
        else:
            require(bool(c.get('limitation')), cid + ': unresolved claim needs limitation')
        if state != 'unreadable':
            require(bool(supports), cid + ': record the source of this claim/lead')

    drugs = index(data.get('drugs', []), 'drugs')
    expected = {tuple(sorted(x)) for x in combinations(drugs, 2)}
    pairs, actual = data.get('pairs', []), []
    for p in pairs:
        ids = p.get('drugs', [])
        valid = len(ids) == 2 and len(set(ids)) == 2 and all(i in drugs for i in ids)
        require(valid, 'pair: exactly two distinct known drugs required')
        pair = tuple(sorted(ids))
        actual.append(pair)
        label = '/'.join(ids)
        search, finding = p.get('search_state'), p.get('search_result')
        state = pair_state(p, claims)
        require(search in ('not_searched', 'searched', 'blocked'), label + ': invalid search state')
        require('evidence_state' not in p and 'evidence_ids' not in p, label + ': use claim_ids; pair evidence status is computed')
        require(finding in ('found', 'not_found', 'unreadable', 'not_searched'), label + ': invalid search_result')
        require(search != 'not_searched', label + ': still unsearched; continue or record an actual blocking condition')
        if search == 'searched':
            require(bool(p.get('search_record')), label + ': actual query/reused result required')
            require(finding != 'not_searched', label + ': inconsistent search/result state')
        if search == 'blocked':
            require(bool(p.get('block_reason')), label + ': real blocking reason required')
            require(finding in ('unreadable', 'not_searched', 'found'), label + ': blocked is not searched-and-not-found')
            warnings.append(label + ': retrieval blocked; disclose incomplete coverage')
        if finding == 'not_found':
            require(search == 'searched', label + ': not_found requires an actual search')
        refs = p.get('claim_ids', [])
        require(isinstance(refs, list) and len(refs) == len(set(refs)), label + ': unique claim_ids list required')
        require(all(i in claims for i in refs), label + ': unknown claim id')
        require(bool(refs) == (finding == 'found'), label + ': found needs linked claims; no finding cannot contain claims')
        case_ids = p.get('case_fact_ids', [])
        require(isinstance(case_ids, list) and all(i in facts for i in case_ids), label + ': unknown case fact reference')
        applicability = p.get('case_applicability')
        require(applicability in ('satisfied', 'partial', 'not_satisfied', 'unknown', 'conflict'), label + ': explicit case applicability required')
        if applicability in ('partial', 'unknown', 'conflict'):
            require(bool(p.get('case_pending')), label + ': unresolved applicability needs case_pending')
        for field in ('evidence_gaps', 'case_pending'):
            require(isinstance(p.get(field), list), label + ': ' + field + ' must be explicit list')
        if state != 'verified':
            require(bool(p.get('evidence_gaps')), label + ': unresolved evidence needs explicit gaps')
        if p.get('evidence_gaps'):
            public = p.get('public_search', {})
            require(public.get('state') in ('attempted', 'unavailable', 'not_needed'), label + ': document public search disposition for remaining gaps')
            require(bool(public.get('record')), label + ': public search needs an actual query/result, availability reason or scoped justification')
            require(bool(p.get('stop_reason')), label + ': explain why remaining evidence work stops')
    require(len(actual) == len(set(actual)), 'pairs: duplicate unordered pair')
    require(set(actual) == expected, 'pairs: missing/extra combinations')

    stats = {
        'expected_pairs':len(expected), 'actual_unique_pairs':len(set(actual)),
        'pairs_with_verified_claim':sum(pair_state(p, claims) == 'verified' for p in pairs),
        'evidence_incomplete_pairs':sum(pair_state(p, claims) != 'verified' or bool(p.get('evidence_gaps')) for p in pairs),
        'case_unresolved_pairs':sum(bool(p.get('case_pending')) for p in pairs),
        'not_found_pairs':sum(p.get('search_result') == 'not_found' for p in pairs),
        'unsearched_pairs':sum(p.get('search_state') == 'not_searched' for p in pairs),
        'blocked_pairs':sum(p.get('search_state') == 'blocked' for p in pairs),
    }
    return {'ok':not errors, 'errors':errors, 'warnings':warnings, 'missing_fact_ids':missing,
            'statistics':stats, 'scope':'Structural checks only; original transcription, source truth, and clinical applicability require review.'}


def tables(data, result):
    def cell(x):
        if isinstance(x, dict) and '_link' in x:
            return '['+escape(x['label']).replace('|', '\\|')+'](<'+x['_link']+'>)'
        return escape(str(x if x is not None else '—')).replace('|', '\\|').replace('\n', '<br>')
    def locator(s):
        value = s.get('locator')
        if s['locator_type'] == 'url':
            return {'_link':value, 'label':value}
        if s['locator_type'] == 'doi':
            return {'_link':'https://doi.org/'+quote(value,safe='/'), 'label':'DOI:'+value}
        if s['locator_type'] == 'pmid':
            return {'_link':'https://pubmed.ncbi.nlm.nih.gov/'+value+'/', 'label':'PMID:'+value}
        return value or '缺少唯一定位'
    def table(headers, rows):
        return '\n'.join('| ' + ' | '.join(cell(c) for c in row) + ' |' for row in [headers, ['---']*len(headers), *rows])
    states = {'provided':'明确记载','missing':'未提供','conflict':'冲突','unreadable':'无法读取'}
    visibility_labels = {'known':'当时已获得','unknown':'可见性未知','not_applicable':'不适用'}
    search_labels = {'searched':'已检索','not_searched':'未检索','blocked':'受阻'}
    applicability_labels = {'satisfied':'满足所列条件','partial':'部分满足','not_satisfied':'不满足所列条件','unknown':'未知','conflict':'冲突'}
    basis_labels = {'direct':'直接支持','secondary':'二次转引'}
    public_labels = {'attempted':'已尝试公开检索','unavailable':'公开能力不可用','not_needed':'本题无需继续公开检索'}
    reading_labels = {'body':'正文','excerpt':'原文片段','abstract':'摘要','generated':'生成答案','secondary':'转引','unreadable':'不可读'}
    stat_labels = {'expected_pairs':'应列唯一药物对','actual_unique_pairs':'实际唯一药物对','pairs_with_verified_claim':'含已核验主张的对数','evidence_incomplete_pairs':'证据未完成对数','case_unresolved_pairs':'病例未决对数','not_found_pairs':'本次未找到对数','unsearched_pairs':'未检索对数','blocked_pairs':'受阻对数'}
    historical = data['time_mode'] == 'historical'
    parts = ['## 时间口径', '历史截止：'+data['cutoff']['value']+'；依据：'+data['cutoff']['quote'] if historical else '当前模式：使用全部已提供资料，无历史截止。']
    section_labels = {'before':'截止前形成/发生','after':'事后','unknown':'时点未知','current':'当前资料'}
    facts = {f['id']:f for f in data.get('facts', [])}
    if data.get('events'):
        for section in (('before','after','unknown') if historical else ('current','unknown')):
            events = [e for e in data['events'] if e['section'] == section]
            if events:
                parts += ['\n## '+section_labels[section], table(['事件','时点','关联原值/事实','可见性'], [
                    [e['id'], e.get('at'), '；'.join(i+' '+facts[i]['object']+'/'+facts[i]['field']+'='+str(facts[i].get('value') or '未知') for i in e['fact_ids']),visibility_labels[e['visibility']]]
                    for e in sorted(events, key=lambda e:e.get('at') or '')])]
    def fact_section(fid):
        sections = {e['section'] for e in data.get('events', []) if fid in e['fact_ids']}
        return '、'.join(section_labels[s] for s in ('before','after','current','unknown') if s in sections) or ('未编码时点，不能据此断言截止前已知' if historical else '当前资料')
    parts += ['## 全部输入字段索引（不表示当时已获得）' if historical else '## 事实字段', table(['ID','对象/字段','原值','状态','时间分区','材料/原文'], [
        [f['id'], f['object']+'/'+f['field'], f.get('value'), states[f['state']],
         fact_section(f['id']),
         '；'.join(q['material']+'：'+q['text'] for q in f.get('quotes', []))] for f in data.get('facts', [])])]
    parts += ['\n## 需补或核对的字段', table(['ID','对象/字段','状态'], [
        [f['id'],f['object']+'/'+f['field'],states[f['state']]] for f in data.get('facts', []) if f['state'] != 'provided'])]
    if data.get('drugs'):
        parts += ['\n## 纳入用药', table(['D','原文名称'], [[d['id'],d['name']] for d in data['drugs']])]
    claims = {c['id']: c for c in data.get('claims', [])}
    claim_labels = {'verified':'已核验此主张','clue':'仅有线索','unreadable':'不可读/未核验'}
    pair_labels = {'verified':'含已核验主张','clue':'仅有线索','not_found':'本次未找到','unreadable':'无法读取','not_searched':'未检索'}
    if claims:
        parts += ['\n## 逐条主张与支持关系', table(['C','主张','状态','条件/限制','支持E/关系/位置/所读原文'], [
            [c['id'], c['text'], claim_labels[c['state']], c['conditions']+'；'+c.get('limitation',''),
             '；'.join(s['source']+' / '+basis_labels[s['basis']]+' / '+s['location']+'：'+s['quote'] for s in c['supports'])]
            for c in claims.values()])]
    if data.get('pairs'):
        parts += ['\n## 药物对状态', table(['药物对','检索状态/记录','计算状态/C','病例适用性/事实','证据缺口','病例未决','公开补证/停止原因'], [
            [' × '.join(p['drugs']), search_labels[p['search_state']]+'：'+str(p.get('search_record') or p.get('block_reason') or ''),
             pair_labels[pair_state(p, claims)]+'：'+','.join(p['claim_ids']),
             applicability_labels[p['case_applicability']]+'：'+','.join(p.get('case_fact_ids', [])), '；'.join(p['evidence_gaps']),
             '；'.join(p['case_pending']), public_labels.get(p.get('public_search', {}).get('state'),'无证据补证缺口')+'：'+p.get('public_search', {}).get('record','')+'；'+p.get('stop_reason','')]
             for p in data['pairs']]),
            '\n## 逐对统计（集合可重叠）', table(['统计项','对数'],[[stat_labels[k],v] for k,v in result['statistics'].items()])]
    if data.get('sources'):
        parts += ['\n## 来源定位与读取层级', table(['E','标题/版本','定位','已读位置/层级','原始工具标识','限制'], [
            [s['id'],s['title']+'；'+s.get('version','日期未提供'), locator(s),
             s.get('location','未提供')+'；'+reading_labels[s['read_level']],s.get('tool_ref') or s.get('tool_ref_note'),s.get('limitation','')]
            for s in data['sources']])]
    fingerprint = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    return BEGIN+'\n<!-- ledger-sha256:'+fingerprint+' -->\n\n'+'\n\n'.join(parts)+'\n\n'+END+'\n'


def check_report(text, expected):
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        return False
    start, end = text.index(BEGIN), text.index(END) + len(END)
    return text[start:end] == expected.rstrip('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ledger', type=Path)
    parser.add_argument('--tables', type=Path, help='write reusable Markdown tables only if checks pass')
    parser.add_argument('--check-report', type=Path, help='check final Markdown core block against this ledger; does not check prose outside it')
    args = parser.parse_args()
    try:
        data = json.loads(args.ledger.read_text(encoding='utf-8'))
        result = validate(data)
        rendered = tables(data, result) if result['ok'] else None
        if result['ok'] and args.check_report:
            matches = check_report(args.check_report.read_text(encoding='utf-8'), rendered)
            result['report_block_matches'] = matches
            if not matches:
                result['ok'] = False
                result['errors'].append('report core missing, duplicated, stale or changed; regenerate from the corrected ledger')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({'ok':False, 'errors':['Invalid ledger: '+str(exc)]},ensure_ascii=False))
        return 2
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if result['ok'] and args.tables:
        args.tables.write_text(rendered,encoding='utf-8')
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
