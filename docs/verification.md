# 검증

> README 에서 옮긴 상세 문서다(2026-11-10). 처음 설치는 [README 의 빠른 시작](../README.md#빠른-시작)을 본다.

## 테스트와 스냅샷 비교

```sh
JARVIS_CONFIG=/dev/null python3 -m unittest discover -s tests   # 파서·플랫폼 층·스냅샷 조립(이 기기 설정 파일과 무관하게)
node tests/test_jarvis_js.mjs                             # 화면 공용 HTML 이스케이프(jarvis.js)
# 시험 포트로 기동해 스키마 비교(9300 은 쓰지 않는다). 기준선 fixtures/ 는 내부 정보라 git 에 없다.
JARVIS_CONFIG=deploy/pit5.toml python3 tools/snapcheck.py --port 9391 \
    --baseline fixtures/pit5-baseline-20260928 --known fixtures/s2-pre-refactor/known.txt
# 기기 환경이 바뀌어(컨테이너·서비스가 사라지는 등) 알려진 차이 목록이 낡으면 실패가 나온다.
# 목록은 반드시 리팩토링 전 코드로 같은 기기에서 다시 만든다(지금 코드로 만들면 회귀도 기기 차이로 묻힌다).
#   git archive 9432919 | tar -x -C "$TMP" && (cd "$TMP" && python3 tools/snapcheck.py --port 9393 --module pit5mon \
#       --baseline <이 저장소>/fixtures/pit5-baseline-20260928 --write-known <이 저장소>/fixtures/s2-pre-refactor/known.txt)
```
