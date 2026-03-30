"""
求人DBのPDFからジョブデータを抽出してjobs_db.jsonに保存するスクリプト。
初回セットアップ時に一度だけ実行してください。

Usage:
    python3 extract_jobs.py
"""
import pdfplumber
import json
import re
import os
import sys

PDF_PATH = os.path.expanduser('~/Desktop/求人DB.pdf')
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), 'jobs_db.json')


def parse_job_line(line):
    """インデックス行を解析して求人情報dictを返す。"""
    # 先頭の * を除去
    line = line.lstrip('* ').strip()

    # レコード番号（4〜5桁）を先頭から取得
    m = re.match(r'^(\d{4,5})\s+', line)
    if not m:
        return None
    record_num = m.group(1)
    rest = line[m.end():].strip()

    # URL を末尾から取得
    url_m = re.search(r'(https?://\S+)\s*$', rest)
    url = url_m.group(1) if url_m else ''
    if url_m:
        rest = rest[:url_m.start()].strip()

    # OPEN_X を探す（日付と結合している場合あり: "2025/10/13OPEN_A"）
    parts = rest.split()
    open_idx = None
    open_judge = ''
    for i, p in enumerate(parts):
        if 'OPEN_' in p:
            open_idx = i
            open_judge = 'OPEN_' + p.split('OPEN_')[1]
            break

    if open_idx is None:
        return None

    after = parts[open_idx + 1:]

    # ランク（S/A/B/C/D/E の1文字）は任意
    rank = ''
    if after and re.match(r'^[SABCDE]$', after[0]):
        rank = after[0]
        after = after[1:]

    # 業種（〜系で終わる）
    industry = ''
    job_type = ''
    company = ''

    for i, p in enumerate(after):
        if p.endswith('系'):
            industry = p
            remaining = after[i + 1:]
            if remaining:
                job_type = remaining[0]
                company = ' '.join(remaining[1:])
            break

    if not industry and after:
        company = ' '.join(after)

    if not company and not industry:
        return None

    return {
        'record_num': record_num,
        'open_judge': open_judge,
        'rank': rank,
        'industry': industry,
        'job_type': job_type,
        'company': company,
        'url': url,
        'description': '',
        'catch_copy': '',
        'education': '',
    }


def extract_index(pdf, max_pages=390):
    """インデックスページから求人一覧を抽出する。"""
    jobs_dict = {}  # record_num -> job

    for page_num in range(min(max_pages, len(pdf.pages))):
        if page_num % 50 == 0:
            print(f'  インデックス: {page_num}/{max_pages} ページ処理中...', flush=True)

        text = pdf.pages[page_num].extract_text()
        if not text:
            continue

        for line in text.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            # ヘッダ・ページ番号行をスキップ
            if 'レコードの開始行' in line or re.match(r'^\d{1,3}$', line):
                continue

            # * で始まる行（新レコード）のみ対象
            if line.startswith('*'):
                job = parse_job_line(line)
                if job and job['record_num'] not in jobs_dict:
                    jobs_dict[job['record_num']] = job

    return list(jobs_dict.values())


def normalize(text):
    """会社名正規化（マッチング用）。"""
    text = re.sub(r'[【】（）()「」『』　 \u3000]', '', text)
    text = re.sub(r'(株式会社|有限会社|合同会社|株)', '', text)
    return text.strip()


def extract_detail_pages(pdf, jobs, start_page=389, max_detail_pages=600):
    """詳細ページから求人説明を抽出してインデックスとマッチングする。"""
    # 会社名 → job のルックアップ（正規化後）
    company_lookup = {}
    for job in jobs:
        norm = normalize(job['company'])
        if norm and len(norm) >= 3:
            company_lookup[norm] = job

    seen_copies = set()
    end_page = min(start_page + max_detail_pages, len(pdf.pages))

    for page_num in range(start_page, end_page):
        if (page_num - start_page) % 100 == 0:
            print(f'  詳細: {page_num - start_page}/{max_detail_pages} ページ処理中...', flush=True)

        text = pdf.pages[page_num].extract_text()
        if not text:
            continue
        text = text.strip()

        # ページ番号のみのページをスキップ
        if re.match(r'^\d{1,4}$', text):
            continue

        lines = text.split('\n')
        # ヘッダ行（訴求ポイント 企業担当 学歴）をスキップ
        if lines and '訴求ポイント' in lines[0]:
            lines = lines[1:]
            text = '\n'.join(lines)

        # 各ブロックを分割（タイトル行：【...】 や ■ で始まる行を基点）
        blocks = split_into_blocks(text)

        for block in blocks:
            if not block.strip():
                continue

            first_line = block.split('\n')[0].strip()

            # 重複除去
            if first_line in seen_copies:
                continue
            seen_copies.add(first_line)

            # 担当者コードと学歴をタイトル行から抽出
            # 形式: "[タイトル] [担当者] [学歴]"
            title_m = re.match(
                r'^(.*?)\s+([a-z_]+)\s+([\S]+(?:以上|不問|可|卒可|マスト).*)$',
                first_line
            )
            catch_copy = ''
            education = ''
            if title_m:
                catch_copy = title_m.group(1).strip()
                education = title_m.group(3).strip()
            else:
                catch_copy = first_line

            # インデックスとマッチング
            matched_job = None
            for norm_name, job in company_lookup.items():
                if norm_name in normalize(block):
                    matched_job = job
                    break
                # キャッチコピー内での検索
                if catch_copy and norm_name in normalize(catch_copy):
                    matched_job = job
                    break

            if matched_job and not matched_job.get('description'):
                desc_lines = block.split('\n')[1:15]  # 最初の15行を取得
                description = '\n'.join(l.strip() for l in desc_lines if l.strip())
                matched_job['description'] = description[:800]
                matched_job['catch_copy'] = catch_copy[:200]
                if education:
                    matched_job['education'] = education

    return jobs


def split_into_blocks(text):
    """テキストを求人ブロックに分割する。"""
    # 新しい求人エントリの開始パターン
    # 【...】 や ■, ☆ で始まる行から新ブロック
    entry_pattern = re.compile(
        r'^(【.+?】|■.{5,}|☆.{3,}|[^\n]{10,}[a-z_]{3,}\s+\S+(?:以上|不問|可|マスト))',
        re.MULTILINE
    )

    positions = [m.start() for m in entry_pattern.finditer(text)]
    if not positions:
        return [text]

    blocks = []
    for i, pos in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        blocks.append(text[pos:end])

    return blocks


def main():
    if not os.path.exists(PDF_PATH):
        print(f'エラー: PDFファイルが見つかりません: {PDF_PATH}')
        sys.exit(1)

    print(f'PDF読み込み中: {PDF_PATH}')
    with pdfplumber.open(PDF_PATH) as pdf:
        total_pages = len(pdf.pages)
        print(f'総ページ数: {total_pages}')

        print('\n[1/2] インデックス抽出中...')
        jobs = extract_index(pdf, max_pages=390)
        print(f'  → {len(jobs)} 件の求人を抽出しました')

        print('\n[2/2] 詳細説明抽出中（最初の600ページ）...')
        jobs = extract_detail_pages(pdf, jobs, start_page=389, max_detail_pages=600)

        with_desc = sum(1 for j in jobs if j.get('description'))
        print(f'  → 説明文あり: {with_desc}/{len(jobs)} 件')

    print(f'\n保存中: {OUTPUT_PATH}')
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)

    print(f'完了！ {len(jobs)} 件の求人データを保存しました。')

    # サマリー表示
    industries = {}
    for job in jobs:
        ind = job.get('industry', '不明')
        industries[ind] = industries.get(ind, 0) + 1
    print('\n業種別件数:')
    for ind, cnt in sorted(industries.items(), key=lambda x: -x[1]):
        print(f'  {ind}: {cnt}件')


if __name__ == '__main__':
    main()
