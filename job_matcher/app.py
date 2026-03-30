"""
面談記録 → 求人推薦アプリ
Nottaの文字起こしを入れると、学生に合う求人を5〜10件推薦する。
"""
import os
import re
import json
import anthropic
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# ===== 設定 =====
JOBS_DB_PATH = os.path.join(os.path.dirname(__file__), 'jobs_db.json')
PDF_PATH = os.path.expanduser('~/Desktop/求人DB.pdf')

# Anthropic クライアント（環境変数 ANTHROPIC_API_KEY が必要）
client = anthropic.Anthropic()


def load_jobs():
    """jobs_db.jsonを読み込む。なければPDFから抽出して生成する。"""
    if not os.path.exists(JOBS_DB_PATH):
        print('jobs_db.jsonが見つかりません。PDFから抽出中...')
        print('（数分かかります。しばらくお待ちください）')
        from extract_jobs import main as extract_main
        extract_main()

    with open(JOBS_DB_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


# 起動時に求人DBを読み込む
JOBS = []
try:
    JOBS = load_jobs()
    print(f'求人DB読み込み完了: {len(JOBS)} 件')
except Exception as e:
    print(f'警告: 求人DB読み込み失敗: {e}')
    print('extract_jobs.py を先に実行してください。')


def format_jobs_for_claude(jobs):
    """Claude に渡す求人リストをテキスト形式にまとめる。"""
    lines = []
    for i, job in enumerate(jobs, 1):
        parts = [
            f"[{i}] {job.get('company', '不明')}",
            f"業種: {job.get('industry', '')}",
            f"職種: {job.get('job_type', '')}",
            f"ランク: {job.get('rank', '')}",
        ]
        if job.get('catch_copy'):
            parts.append(f"特徴: {job['catch_copy'][:120]}")
        elif job.get('description'):
            first_line = job['description'].split('\n')[0][:120]
            parts.append(f"特徴: {first_line}")
        if job.get('education'):
            parts.append(f"学歴: {job['education']}")
        if job.get('url'):
            parts.append(f"URL: {job['url']}")
        lines.append(' | '.join(parts))
    return '\n'.join(lines)


SYSTEM_PROMPT = """あなたは就職活動を支援するキャリアアドバイザーです。
学生との面談内容（Notta文字起こし）を分析し、求人DBから最適な求人を推薦してください。

以下の形式でJSONを返してください（他のテキストは不要）：

{
  "summary": "面談内容の要約（3〜5文）",
  "axes": ["就活軸1", "就活軸2", "就活軸3"],
  "ng_conditions": ["NG条件1", "NG条件2"],
  "suitable_industries": ["向いてる業界1", "向いてる業界2"],
  "recommendations": [
    {
      "rank": 1,
      "company": "会社名",
      "industry": "業種",
      "job_type": "職種",
      "url": "URL（あれば）",
      "reason": "この求人を推薦する理由（学生の軸・特徴と求人の一致点を具体的に）"
    }
  ]
}

推薦は5〜10件、reasonは必ず面談内容と求人の具体的なマッチング理由を書いてください。"""


@app.route('/')
def index():
    return render_template('index.html', jobs_count=len(JOBS))


@app.route('/analyze', methods=['POST'])
def analyze():
    transcript = request.form.get('transcript', '').strip()

    if not transcript:
        return jsonify({'error': '文字起こしを入力してください。'}), 400

    if not JOBS:
        return jsonify({'error': '求人DBが読み込まれていません。extract_jobs.py を実行してください。'}), 500

    jobs_text = format_jobs_for_claude(JOBS)

    user_message = f"""【面談文字起こし】
{transcript[:8000]}

---
【求人リスト（全{len(JOBS)}件）】
{jobs_text}

上記の求人リストから、この学生に最も合う求人を5〜10件選んで推薦してください。"""

    try:
        response = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{'role': 'user', 'content': user_message}]
        )

        raw = response.content[0].text.strip()

        # JSONブロックが```json...```で囲まれている場合に対応
        if raw.startswith('```'):
            raw = re.sub(r'^```(?:json)?\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)

        result = json.loads(raw)
        return jsonify(result)

    except json.JSONDecodeError as e:
        return jsonify({'error': f'Claude の応答解析エラー: {e}', 'raw': raw}), 500
    except anthropic.APIError as e:
        return jsonify({'error': f'APIエラー: {e}'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    debug = os.environ.get('FLASK_ENV') != 'production'
    app.run(debug=debug, host='0.0.0.0', port=port)
