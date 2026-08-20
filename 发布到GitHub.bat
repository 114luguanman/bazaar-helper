@echo off
rem 一键开源发布：创建GitHub仓库并推送（需要Personal Access Token）
cd /d %~dp0
chcp 65001 >nul
echo ============================================
echo  大巴扎小帮手 - 一键开源发布
echo ============================================
echo.
echo 需要 GitHub Personal Access Token：
echo   https://github.com/settings/tokens
echo   生成时勾选 repo 权限
echo.
python publish_github.py
echo.
pause
