"""Objective checks, separate human review, and a local HTML result viewer."""
import html
import json
from pathlib import Path
import re

STATUS = {'ok':'自然終了', 'truncated':'文脈／出力上限', 'timeout':'時間制限', 'cancelled':'中断',
          'error':'応答エラー', 'disconnected':'通信切断', 'no_answer':'本文なし',
          'input_too_long':'入力が長すぎる', 'not_run':'未実行', 'setup_error':'起動・準備失敗'}


def latest_rows(rows):
    latest = {}
    for row in rows:
        if 'question_id' in row:
            latest[(row['model_id'],row['device'],row['question_id'],row['repeat'],row.get('turn',1))] = row
    return list(latest.values())


def evaluate_answer(question, row):
    answer = row.get('answer','')
    checks = [{'name':'本文あり','passed':bool(answer.strip())},
              {'name':'APIが自然終了','passed':row['status']=='ok'}]
    if len(answer) >= 200:
        windows = [answer[i:i+40] for i in range(0,len(answer)-39,40)]
        checks.append({'name':'長い文章の過剰な反復なし（警告）','passed':max((windows.count(w) for w in set(windows)),default=0)<5})
    config = question.get('checks', {})
    for group in config.get('required_any', []):
        checks.append({'name':'必要語: '+ ' / '.join(group), 'passed':any(term.casefold() in answer.casefold() for term in group)})
    for term in config.get('forbidden', []):
        checks.append({'name':'禁止語を含まない: '+term, 'passed':term.casefold() not in answer.casefold()})
    if config.get('min_characters'):
        checks.append({'name':f"本文{config['min_characters']}文字以上", 'passed':len(answer)>=config['min_characters']})
    if question['category'] == '翻訳':
        # This is only an omission warning, not a semantic quality score.
        source = question.get('prompt','').split('資料：\n',1)[-1]
        numbers = sorted(set(re.findall(r'(?<!\w)\d+(?:[.,]\d+)*(?!\w)',source)))
        missing = [number for number in numbers if number not in answer]
        checks.append({'name':'原文の数値を保持（欠落候補）','passed':not missing,'missing':missing})
    return {'checks':checks, 'objective_passed':all(c['passed'] for c in checks),
            'human_review_required':True, 'note':'自動チェックは事実性・翻訳網羅性・文章品質の合格判定ではない。'}


def write_reports(directory, rows, metadata):
    effective = latest_rows(rows)
    review_path = directory/'quality-review.json'
    reviews = json.loads(review_path.read_text()) if review_path.exists() else {'schema_version':1,'rubric':
                {'task':'0=未達、1=一部達成、2=達成','japanese':'0=難読、1=改善要、2=自然','instruction':'0=不適合、1=一部適合、2=適合'},'reviews':[]}
    known = {(r['model_id'],r['device'],r['question_id'],r.get('turn',1),r.get('attempt',1)) for r in reviews['reviews']}
    for row in effective:
        key = (row['model_id'],row['device'],row['question_id'],row.get('turn',1),row.get('attempt',1))
        if row['repeat'] == 1 and key not in known:
            reviews['reviews'].append({k:row.get(k) for k in ('model_id','device','question_id','turn','attempt')} |
                                    {'task':None,'japanese':None,'instruction':None,'translation_omissions':None,'notes':''})
            known.add(key)
    review_path.write_text(json.dumps(reviews, ensure_ascii=False, indent=2)+'\n')
    esc = lambda value: html.escape(str(value))
    seconds = lambda value: "—" if value is None else f"{float(value):.2f}"
    cards = []
    for row in effective:
        checks = row.get('quality_checks',{}).get('checks',[])
        check_text = ' / '.join(('✓ ' if c['passed'] else '要確認: ')+c['name']+(' ['+', '.join(c['missing'])+']' if c.get('missing') else '') for c in checks)
        human = next((r for r in reviews['reviews'] if r['model_id']==row['model_id'] and r['device']==row['device'] and
                      r['question_id']==row['question_id'] and r.get('turn',1)==row.get('turn',1) and r.get('attempt',1)==row.get('attempt',1)), None) if row['repeat']==1 else None
        human_text = '未評価' if not human or human['task'] is None else f"課題 {human['task']}/2、日本語 {human['japanese']}/2、指示 {human['instruction']}/2"
        cards.append(f"<article data-status='{esc(row['status'])}'><h2>{esc(row['model_id'])} / {esc(row['device'].upper())} / {esc(row['question_id'])}</h2>"
                     f"<p>{esc(STATUS.get(row['status'],row['status']))} · {row['repeat']}回目 · 会話{row.get('turn',1)} · 試行{row.get('attempt',1)} · "
                     f"所要{seconds(row.get('total_seconds'))}秒 · 本文開始{seconds(row.get('ttfa_seconds'))}秒</p>"
                     f"<p>自動確認: {esc(check_text)}<br>人間の評価: {esc(human_text)}</p>"
                     f"<p class='error'>{esc(row.get('error') or '')}</p><details><summary>入力（全文）</summary><pre>{esc(row.get('prompt',''))}</pre></details>"
                     f"<details><summary>回答 {row.get('answer_characters',0)}文字</summary><pre>{esc(row.get('answer',''))}</pre></details>"
                     f"<details><summary>思考・途中の思考</summary><pre>{esc(row.get('reasoning',''))}</pre></details></article>")
    sessions = ''.join(f"<li>起動記録{index}: {esc(s['model_id'])} / {esc(s['device'].upper())}: 起動 {seconds(s.get('startup_seconds'))}秒、最大RSS "
                       f"{(format(s['peak_process_rss_bytes']/1024**3,'.2f')+' GiB') if s.get('peak_process_rss_bytes') is not None else '未取得'}（{s.get('resource_samples',0)}標本）</li>" for index,s in enumerate(metadata.get('sessions',[]),1))
    stats = json.loads((directory/'summary.json').read_text()) if (directory/'summary.json').exists() else []
    statistics_html = ''.join(f"<li>{esc(item['model_id'])} / {esc(item['device'].upper())} / {esc(item['question_id'])} / 会話{item.get('turn',1)}: "
                             f"自然終了{item['completed_answers']}/{item['samples']}件、未実行{item['not_run_samples']}件 / "
                             f"全体時間 中央値{seconds(item.get('median_total_seconds'))}秒・平均{seconds(item.get('mean_total_seconds'))}秒・標準偏差{seconds(item.get('stddev_total_seconds'))}秒</li>" for item in stats)
    n = len(effective)
    ok = sum(r['status']=='ok' for r in effective)
    page = f'''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>ローカルLLM測定結果</title>
<style>body{{font:16px system-ui;max-width:1080px;margin:40px auto;padding:0 20px;background:#f5f6f8;color:#17202b}}article{{background:white;padding:20px;margin:16px 0;border-radius:12px}}h2{{font-size:18px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}summary{{cursor:pointer;padding:8px}}.error{{color:#b52727}}button{{padding:10px;margin:6px}}</style>
<h1>ローカルLLM測定結果</h1><p>{n}件中、自然終了 {ok}件。自然終了と課題達成・回答品質は別に評価します。途中回答も保存しています。</p>
<p>人間の評価は quality-review.json に記入し、再表示は <code>.venv/bin/python scripts/render_report.py 実行フォルダ</code>。</p>
<details><summary>集計（自然終了のみ）</summary><ul>{statistics_html}</ul></details>
<ul>{sessions}</ul><p>RSSはGPU/共有メモリの全使用量ではありません。システムの圧迫・swapは resources.jsonl を確認してください。</p>
<input id="search" placeholder="モデル・課題を絞り込み" aria-label="モデル・課題を絞り込み"><label><input type="checkbox" id="failed">自然終了以外のみ</label>
{''.join(cards)}<script>function filter(){{document.querySelectorAll('article').forEach(a=>a.hidden=(!a.textContent.toLowerCase().includes(document.querySelector('#search').value.toLowerCase())||(document.querySelector('#failed').checked&&a.dataset.status==='ok')))}}document.querySelector('#search').oninput=filter;document.querySelector('#failed').onchange=filter;</script></html>'''
    (directory/'report.html').write_text(page, encoding='utf-8')
