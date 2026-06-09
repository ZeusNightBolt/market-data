#!/usr/bin/env python3
"""SEC fallback + final No Data tag for ticker_classification_cache unresolved rows."""
from __future__ import annotations
import json, re, time
from datetime import datetime, timezone
from pathlib import Path
import requests, duckdb, pandas as pd

DB = Path('/home/nima/market-data/market_data.duckdb')
OUT = Path('/home/nima/market-data/raw/classification/sec_fallback_final.ndjson')
UA = {'User-Agent': 'Hermes Research contact@example.com'}
SIC_TO_SECTOR = {
    '01':'Consumer Staples','02':'Consumer Staples','07':'Consumer Staples','08':'Materials','09':'Consumer Staples',
    '10':'Materials','12':'Energy','13':'Energy','14':'Materials','15':'Industrials','16':'Industrials','17':'Industrials',
    '20':'Consumer Staples','21':'Consumer Staples','22':'Consumer Discretionary','23':'Consumer Discretionary','24':'Materials','25':'Consumer Discretionary','26':'Materials','27':'Communication Services','28':'Healthcare','29':'Energy','30':'Materials','31':'Consumer Discretionary','32':'Materials','33':'Materials','34':'Industrials','35':'Information Technology','36':'Information Technology','37':'Industrials','38':'Healthcare','39':'Consumer Discretionary','40':'Industrials','41':'Industrials','42':'Industrials','44':'Industrials','45':'Industrials','47':'Industrials','48':'Communication Services','49':'Utilities','50':'Industrials','51':'Consumer Staples','52':'Consumer Discretionary','53':'Consumer Discretionary','54':'Consumer Staples','55':'Consumer Discretionary','56':'Consumer Discretionary','57':'Consumer Discretionary','58':'Consumer Discretionary','59':'Consumer Discretionary','60':'Financials','61':'Financials','62':'Financials','63':'Financials','64':'Financials','65':'Real Estate','67':'Financials','70':'Consumer Discretionary','72':'Consumer Discretionary','73':'Information Technology','75':'Consumer Discretionary','78':'Communication Services','79':'Communication Services','80':'Healthcare','81':'Industrials','82':'Consumer Discretionary','83':'Healthcare','87':'Industrials'
}

def norm(s):
    return re.sub(r'[^A-Z0-9]+','', (s or '').upper())

def fetch_sic(cik):
    cik10=f'{int(cik):010d}'
    url=f'https://data.sec.gov/submissions/CIK{cik10}.json'
    r=requests.get(url,headers=UA,timeout=15)
    if r.status_code != 200:
        return None
    d=r.json()
    sic=str(d.get('sic') or '').strip() or None
    desc=d.get('sicDescription') or None
    return {'cik':cik10,'name':d.get('name'),'sic_code':sic,'sic_description':desc,'tickers':d.get('tickers'),'exchanges':d.get('exchanges')}

def main():
    con=duckdb.connect(str(DB))
    unresolved=con.execute("""
        SELECT v.ticker, v.holding_name
        FROM vti_daily_enriched_latest v
        LEFT JOIN ticker_classification_cache c ON c.ticker=v.ticker
        WHERE (c.sector IS NULL OR c.sector='') AND (c.industry IS NULL OR c.industry='')
        ORDER BY v.ticker
    """).fetchall()
    print(f'unresolved before SEC fallback: {len(unresolved)}')
    if not unresolved:
        return
    sec=requests.get('https://www.sec.gov/files/company_tickers.json',headers=UA,timeout=20).json()
    by_ticker={v['ticker'].upper():v for v in sec.values()}
    by_title={norm(v['title']):v for v in sec.values()}
    ts=datetime.now(timezone.utc).replace(tzinfo=None)
    final_stale=datetime(2099,1,1)
    rows=[]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('a') as f:
        for ticker, holding_name in unresolved:
            match=None; match_method=None
            if ticker.upper() in by_ticker:
                match=by_ticker[ticker.upper()]; match_method='ticker_exact'
            else:
                hn=norm(holding_name)
                if hn in by_title:
                    match=by_title[hn]; match_method='title_exact'
            row={'ticker':ticker,'holding_name':holding_name,'sec_match':match,'match_method':match_method}
            if match:
                time.sleep(0.13)
                sic=fetch_sic(match['cik_str'])
                row['sec_submissions']=sic
                sector=SIC_TO_SECTOR.get((sic or {}).get('sic_code','')[:2]) if sic else None
                industry=(sic or {}).get('sic_description')
                if sector or industry:
                    out={
                        'ticker':ticker,'sector':sector,'industry':industry,'gics_sector':sector,
                        'sic_code':(sic or {}).get('sic_code'),'sic_description':industry,
                        'source':'sec_submissions_sic_fallback','source_priority':60,'confidence':0.55,
                        'raw_json':json.dumps(row,default=str),'enriched_at':ts,'stale_after':final_stale,'error':None
                    }
                else:
                    out={
                        'ticker':ticker,'sector':'No Data','industry':'No Data','source':'sec_no_reliable_data_final',
                        'source_priority':1000,'confidence':0.0,'raw_json':json.dumps(row,default=str),
                        'enriched_at':ts,'stale_after':final_stale,'error':'FINAL_NO_RELIABLE_SEC_OR_YFINANCE_DATA'
                    }
            else:
                out={
                    'ticker':ticker,'sector':'No Data','industry':'No Data','source':'sec_no_reliable_data_final',
                    'source_priority':1000,'confidence':0.0,'raw_json':json.dumps(row,default=str),
                    'enriched_at':ts,'stale_after':final_stale,'error':'FINAL_NO_SEC_MATCH_OR_YFINANCE_DATA'
                }
            rows.append(out)
            f.write(json.dumps(out,default=str)+'\n')
            print(ticker, match_method or 'no_sec_match', out['sector'], out['industry'], out['error'])
    df=pd.DataFrame(rows)
    con.register('sec_final_stage', df)
    cols=['ticker','sector','industry','sub_industry','gics_sector','gics_industry_group','gics_industry','gics_sub_industry','sic_code','sic_description','country','source','source_priority','confidence','raw_json','enriched_at','stale_after','error']
    for c in cols:
        if c not in df.columns: df[c]=None
    con.unregister('sec_final_stage')
    con.register('sec_final_stage', df[cols])
    con.execute('BEGIN')
    con.execute("""
      DELETE FROM ticker_classification_cache t
      USING sec_final_stage s
      WHERE t.ticker=s.ticker
        AND ((t.sector IS NULL OR t.sector='') AND (t.industry IS NULL OR t.industry=''))
    """)
    con.execute(f"INSERT INTO ticker_classification_cache ({','.join(cols)}) SELECT {','.join(cols)} FROM sec_final_stage")
    con.execute('COMMIT')
    print('\nfinal coverage')
    print(con.execute("""
      SELECT COUNT(*) AS cache_rows,
             COUNT(*) FILTER (WHERE sector IS NOT NULL AND sector <> '') AS with_sector,
             COUNT(*) FILTER (WHERE sector='No Data') AS no_data_rows,
             COUNT(*) FILTER (WHERE source='sec_submissions_sic_fallback') AS sec_success_rows
      FROM ticker_classification_cache
    """).fetchdf().to_string(index=False))
    print('\ncurrent VTI view')
    print(con.execute("""
      SELECT COUNT(*) AS current_vti_rows,
             COUNT(*) FILTER (WHERE sector IS NOT NULL AND sector <> '') AS with_sector,
             COUNT(*) FILTER (WHERE sector='No Data') AS no_data_rows
      FROM v_vti_ticker_classification
    """).fetchdf().to_string(index=False))
    print(f'raw log: {OUT}')
    con.close()
if __name__=='__main__': main()
