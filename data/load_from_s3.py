import psycopg2
import boto3
import json
import uuid
import re
from dotenv import load_dotenv
import os

load_dotenv()

def connect_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'),
        port=os.getenv('POSTGRES_PORT'),
        dbname=os.getenv('POSTGRES_DB'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD')
    )

def connect_s3():
    return boto3.client('s3',
        region_name=os.getenv('AWS_REGION')
    )

def parse_limitations(claim_text):
    if not claim_text:
        return []
    text = re.sub(r'^\d+\.\s*', '', claim_text).strip()
    parts = text.split(';')
    limitations = []
    for part in parts:
        part = re.sub(r'\s+', ' ', part).strip()
        if len(part) >= 10:
            limitations.append(part)
    return limitations

def parse_claims(claim_text):
    if not claim_text:
        return []
    claim_text = re.sub(
        r'^.*?(?=\b1\s*\.)', '', claim_text, flags=re.DOTALL
    ).strip()
    parts = re.split(r'\b(\d+)\s*\.\s*', claim_text)
    claims = []
    i = 1
    while i < len(parts) - 1:
        try:
            claim_no = int(parts[i])
            claim_body = parts[i + 1].strip() \
                         if i + 1 < len(parts) else ''
            if not claim_body:
                i += 2
                continue
            full_claim_text = f"{claim_no}. {claim_body}"
            parent_match = re.search(
                r'\b(?:of|in|to|according\s+to|as\s+(?:in|recited\s+in))\s+claims?\s+(\d+)',
                claim_body, re.IGNORECASE
            )
            parent_claim_no = int(parent_match.group(1)) \
                              if parent_match else None
            is_independent = parent_claim_no is None
            claims.append({
                'claim_no':        claim_no,
                'claim_text':      full_claim_text,
                'is_independent':  is_independent,
                'parent_claim_no': parent_claim_no
            })
        except (ValueError, IndexError):
            pass
        i += 2
    return claims

def load_from_s3(filename):
    s3 = connect_s3()
    response = s3.get_object(
        Bucket=os.getenv('S3_BUCKET_NAME'),
        Key=filename
    )
    rows = json.loads(response['Body'].read().decode('utf-8'))
    print(f"✅ S3에서 {len(rows)}건 로드")
    return rows

def save_to_db(rows):
    conn = connect_db()
    cursor = conn.cursor()

    success = 0
    fail = 0

    for row in rows:
        pub_num = row['publication_number']
        claim_text = row.get('claim_text') or ''

        try:
            # ── documents 저장 ──
            cursor.execute(
                "SELECT document_id FROM documents WHERE ext_id = %s",
                (pub_num,)
            )
            existing = cursor.fetchone()

            if existing:
                document_id = existing[0]
            else:
                document_id = str(uuid.uuid4())

                try:
                    year = int(str(row['filing_date'])[:4])
                    freshness = 0.8 if year >= 2022 else 0.5
                except:
                    freshness = 0.5

                meta = json.dumps({
                    'assignee':    row.get('assignee') or '(출원인 미상)',
                    'filing_date': str(row['filing_date']),
                    'grant_date':  str(row.get('grant_date')),
                    'cpc_code':    row.get('cpc_code')
                }, ensure_ascii=False)

                cursor.execute("""
                    INSERT INTO documents (
                        document_id, source_type, ext_id,
                        title, clean_text, meta,
                        reliability_score, freshness_score,
                        is_user_provided
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING document_id
                """, (
                    document_id, 'patent', pub_num,
                    row.get('title') or '(제목 없음)',
                    row.get('abstract') or '(초록 없음)',
                    meta,
                    0.9, freshness, False
                ))
                document_id = cursor.fetchone()[0]

            # ── patent_claims + claim_limitations 저장 ──
            parsed_claims = parse_claims(claim_text)

            if not parsed_claims:
                print(f"⚠️  {pub_num}: 청구항 파싱 실패")

            for c in parsed_claims:
                # 이미 있는 청구항이면 스킵
                cursor.execute("""
                    SELECT claim_id FROM patent_claims
                    WHERE document_id = %s AND claim_no = %s
                """, (document_id, c['claim_no']))
                existing_claim = cursor.fetchone()

                if existing_claim:
                    continue

                claim_id = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO patent_claims (
                        claim_id, document_id,
                        claim_no, claim_text,
                        is_independent, parent_claim_no
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING claim_id
                """, (
                    claim_id, document_id,
                    c['claim_no'], c['claim_text'],
                    c['is_independent'], c['parent_claim_no']
                ))
                saved_claim_id = cursor.fetchone()[0]

                limitations = parse_limitations(c['claim_text'])
                for order, limitation_text in enumerate(limitations, 1):
                    # 이미 있는 limitation이면 스킵
                    cursor.execute("""
                        SELECT limitation_id FROM claim_limitations
                        WHERE claim_id = %s AND limitation_order = %s
                    """, (saved_claim_id, order))
                    existing_limitation = cursor.fetchone()

                    if existing_limitation:
                        continue

                    limitation_id = str(uuid.uuid4())
                    cursor.execute("""
                        INSERT INTO claim_limitations (
                            limitation_id, claim_id,
                            limitation_order, normalized_text
                        )
                        VALUES (%s, %s, %s, %s)
                    """, (
                        limitation_id, saved_claim_id,
                        order, limitation_text
                    ))

            conn.commit()
            success += 1

        except Exception as e:
            print(f"❌ 저장 실패: {pub_num} → {e}")
            conn.rollback()
            fail += 1

    cursor.close()
    conn.close()
    print(f"✅ 저장 완료: {success}건 성공 / {fail}건 실패")

def verify():
    conn = connect_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM documents WHERE source_type = 'patent'")
    doc_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM patent_claims")
    claim_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM patent_claims WHERE is_independent = TRUE")
    independent_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM claim_limitations")
    limitation_count = cursor.fetchone()[0]

    cursor.close()
    conn.close()

    print("\n=== 저장 결과 확인 ===")
    print(f"documents 테이블        : {doc_count}건")
    print(f"patent_claims 테이블    : {claim_count}건")
    print(f"독립항                  : {independent_count}건")
    print(f"claim_limitations 테이블: {limitation_count}건")

    if claim_count > 0:
        ratio = independent_count / claim_count * 100
        status = "✅ 정상" if 10 <= ratio <= 30 else "⚠️  비정상 (정규식 확인 필요)"
        print(f"독립항 비율             : {ratio:.1f}% {status}")

    print("========================")
    print(f"{doc_count}건 적재 완료")

if __name__ == "__main__":
    filename = "raw/patents/patents_20260615_173739.json"  # ← 여기 변경

    rows = load_from_s3(filename)
    save_to_db(rows)
    verify()