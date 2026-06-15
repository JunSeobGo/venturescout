from google.cloud import bigquery
import boto3
import json
from datetime import datetime
from dotenv import load_dotenv
import os

load_dotenv()

def connect_s3():
    return boto3.client('s3',
        region_name=os.getenv('AWS_REGION')
    )

def fetch_and_backup(limit=10):
    """BigQuery → S3 (한 번만 실행)"""
    client = bigquery.Client()

    query = f"""
    SELECT DISTINCT
    pub.publication_number,
    (SELECT text FROM UNNEST(pub.title_localized)
    WHERE language = 'en' LIMIT 1) AS title,
    (SELECT text FROM UNNEST(pub.abstract_localized)
    WHERE language = 'en' LIMIT 1) AS abstract,
    pub.filing_date,
    pub.grant_date,
    pub.assignee_harmonized[SAFE_OFFSET(0)].name AS assignee,
    (SELECT cpc.code FROM UNNEST(pub.cpc) AS cpc
    WHERE cpc.code LIKE 'G06Q30%' LIMIT 1) AS cpc_code,
    (SELECT claim.text FROM UNNEST(pub.claims_localized) AS claim
    WHERE claim.language = 'en' LIMIT 1) AS claim_text
    FROM `patents-public-data.patents.publications` AS pub
    WHERE EXISTS (
    SELECT 1 FROM UNNEST(pub.cpc) AS cpc
    WHERE cpc.code LIKE 'G06Q30%'
    )
    AND pub.country_code = 'US'
    AND pub.filing_date >= 20250101
    AND pub.filing_date <= 20260611
    LIMIT {limit}
    """

    print(f"BigQuery 쿼리 실행 중... (최대 {limit}건)")
    rows = [dict(row) for row in client.query(query).result()]
    print(f"✅ {len(rows)}건 가져옴")

    # S3에 저장
    s3 = connect_s3()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"raw/patents/patents_{timestamp}.json"

    s3.put_object(
        Bucket=os.getenv('S3_BUCKET_NAME'),
        Key=filename,
        Body=json.dumps(rows, default=str,
                        ensure_ascii=False).encode('utf-8'),
        ContentType='application/json'
    )
    print(f"✅ S3 저장 완료: {filename}")
    print(f"→ 이 파일명을 load_from_s3.py에서 사용하세요")
    return filename

if __name__ == "__main__":
    fetch_and_backup(limit=10)