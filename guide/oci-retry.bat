@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title Oracle 무료 서버 자동 재시도
cd /d "%USERPROFILE%"
set PY=python
where python >nul 2>nul || set PY=py
where %PY% >nul 2>nul || (echo 파이썬이 설치되지 않았습니다. 설치할 때 "Add python.exe to PATH"를 체크했는지 확인하세요. & pause & exit /b 1)
echo [1/3] 오라클 도구 준비 중... (처음에는 1~2분)
%PY% -m pip install --quiet --disable-pip-version-check --upgrade oci
echo [2/3] 자동 재시도 명령 받는 중...
curl -s -o oci_retry.py https://michinkimjjs-lgtm.github.io/guide/oci_retry.py
echo [3/3] 시작합니다. 이 창을 닫지 마세요.
%PY% oci_retry.py
pause
