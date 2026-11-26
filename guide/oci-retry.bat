@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title Oracle 무료 서버 자동 재시도
cd /d "%USERPROFILE%"
set PY=
py -c "import sys" >nul 2>nul && set PY=py
if not defined PY python -c "import sys" >nul 2>nul && set PY=python
if not defined PY (echo 파이썬이 설치되어 있지 않습니다. 안내받은 파이썬 설치 파일로 설치할 때 "Add python.exe to PATH"를 체크하세요. & pause & exit /b 1)
echo [1/3] 오라클 도구 준비 중... (처음에는 3~5분, 아래에 진행 글자가 나옵니다)
%PY% -m pip install --disable-pip-version-check --upgrade oci
echo [2/3] 자동 재시도 명령 받는 중...
curl -s -o oci_retry.py https://michinkimjjs-lgtm.github.io/guide/oci_retry.py
echo [3/3] 시작합니다. 이 창을 닫지 마세요.
%PY% oci_retry.py
pause
