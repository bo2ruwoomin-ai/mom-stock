# 장세 관제실

가족용 주식 정보 사이트. 코스피·코스닥 전 종목과 주요 미국 주식·ETF의 시세, 일봉 차트, 참고 가격대(살 만한 가격·목표가·손절가), 매일 바뀌는 추천과 이유를 보여 줍니다.

- 사이트: https://bo2ruwoomin-ai.github.io/mom-stock/
- 갱신: GitHub Actions가 국내장 중 약 10분, 미국장 중 약 20분마다 시세를 받아 다시 배포합니다 (`.github/workflows/update.yml`).
- 데이터: 국내 실시간 네이버 금융, 일봉 FinanceData/marcap, 해외 Yahoo Finance. 추천 문구는 Claude가 매일 `state/editorial.json`에 씁니다.

투자 권유가 아니며, 실제 매매 전 증권사 앱의 시세를 확인하세요.
