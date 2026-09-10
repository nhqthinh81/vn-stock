@echo off
rem ============================================================================
rem  Chrome danh rieng cho dat lenh tu dong VPS SmartPro.
rem  Gop 2 viec vao 1 file (10/09/2026): KIEM TRA truoc, chi MO khi can, roi
rem  KIEM TRA LAI de xac nhan that su san sang.
rem
rem  Ly do phai kiem tra lai sau khi mo: ban cu chi "start chrome roi mac ke".
rem  Su co 08/09/2026 - Chrome giu dung cong 9222 nhung dang o SmartOne chu
rem  khong phai SmartPro - keo dai 10 ngay vi khong co buoc xac nhan nao.
rem
rem  Logic kiem tra dung chung file Kiem_Tra_Chrome_AutoTrade.ps1 (chi doc
rem  http://127.0.0.1:9222/json, khong dung Playwright, khong dam vao trang).
rem  KHONG chep logic sang day - mot ban duy nhat de khong bao gio lech nhau.
rem
rem  Ma thoat cua .ps1:  0 = san sang | 1 = cong dong/khong co tab | 2 = sai trang
rem ============================================================================

set PROFILE=%LOCALAPPDATA%\VNInvest\ChromeAutoTrade
set CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe
set SMARTPRO=https://smartpro.vps.com.vn/v1/
set CHECK=%~dp0Kiem_Tra_Chrome_AutoTrade.ps1

rem Thieu file kiem tra thi powershell van thoat voi ma 0, khien file nay
rem bao nham "SAN SANG" trong khi chua he kiem duoc gi. Chan trang thai do.
if not exist "%CHECK%" (
    echo.
    echo KHONG tim thay file kiem tra:
    echo   %CHECK%
    echo File nay phai nam CUNG THU MUC voi Kiem_Tra_Chrome_AutoTrade.ps1.
    goto :ket_thuc
)

echo.
echo [Buoc 1/3] Kiem tra Chrome AutoTrade dang chay hay chua...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%CHECK%"
if not errorlevel 1 goto :san_sang

echo.
echo [Buoc 2/3] Chua san sang - dang mo/dieu huong Chrome ve SmartPro...
echo.
if not exist "%PROFILE%" mkdir "%PROFILE%"
if not exist "%CHROME%" (
    echo KHONG tim thay Chrome tai: %CHROME%
    echo Sua duong dan CHROME o dau file nay cho dung may ban.
    goto :ket_thuc
)
rem Chrome dung chung --user-data-dir: neu cua so do DANG chay thi lenh nay
rem chi them 1 tab SmartPro vao chinh no (giu nguyen cong 9222 dang mo), con
rem neu chua chay thi mo cua so moi kem cong debug. Mot lenh xu ly ca 2 truong hop.
start "" "%CHROME%" ^
  --remote-debugging-port=9222 ^
  --user-data-dir="%PROFILE%" ^
  --no-first-run --no-default-browser-check ^
  "%SMARTPRO%"

echo Cho Chrome khoi dong va nap trang...
rem `timeout` chet ngay neu stdin bi chuyen huong (chay tu script khac);
rem `ping` la duong lui van cho dung so giay trong moi hoan canh.
timeout /t 7 /nobreak >nul 2>&1 || ping -n 8 127.0.0.1 >nul

echo.
echo [Buoc 3/3] Kiem tra lai...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%CHECK%"
if not errorlevel 1 goto :san_sang

rem Lan dau chay profile moi, Chrome co the khoi dong cham hon 7 giay.
echo.
echo Chua thay tab SmartPro - cho them 8 giay roi kiem lan cuoi...
timeout /t 8 /nobreak >nul 2>&1 || ping -n 9 127.0.0.1 >nul
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%CHECK%"
if not errorlevel 1 goto :san_sang

echo.
echo ====================================================================
echo  VAN CHUA SAN SANG. Hai nguyen nhan thuong gap:
echo   1) Cong 9222 dang bi mot tien trinh KHAC chiem (Chrome nay khong
echo      bind duoc cong) - dong het cua so Chrome AutoTrade cu roi chay lai.
echo   2) Cua so vua mo dang o trang khac - go dia chi %SMARTPRO%
echo      vao chinh cua so do.
echo ====================================================================
goto :ket_thuc

:san_sang
rem Chot chan cuoi: KHONG BAO GIO de lai 2 tab SmartPro. `check_session()` tu
rem choi dat lenh khi thay nhieu tab (tranh chon nham tai khoan) - tuc la mot tab
rem thua lam bot ngung giao dich ca phien. Ngay 10/09 chinh script nay tao ra tab
rem thua do checker con so URL (tab dang tai co URL rong). Giu tab CU NHAT vi do
rem la tab da dang nhap va da duoc xac minh.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { $t = Invoke-RestMethod 'http://127.0.0.1:9222/json' -TimeoutSec 5 } catch { exit 0 };" ^
  "$p = @($t | Where-Object { $_.type -eq 'page' -and ($_.url -like '*smartpro.vps.com.vn*' -or $_.title -like '*SmartPro*') });" ^
  "if($p.Count -le 1){ exit 0 };" ^
  "Write-Host ('  [don dep] Thay ' + $p.Count + ' tab SmartPro - dong bot, chi giu 1.') -ForegroundColor Yellow;" ^
  "$p | Select-Object -Skip 1 | ForEach-Object { try { Invoke-RestMethod ('http://127.0.0.1:9222/json/close/' + $_.id) -TimeoutSec 5 | Out-Null } catch {} }"

echo.
echo  [OK] Cua so 1/2 - Chrome AutoTrade (SmartPro) san sang.
echo       Nho dang nhap SmartPro + nhap PIN, va DE NGUYEN cua so do.
echo       Phien SmartPro tu het han sau 720 phut (~12 tieng).

rem ============================================================================
rem  CUA SO 2: app Streamlit (tab Phai Sinh).
rem  BAT BUOC phai co, khong phai cho dep: engine phai sinh song trong
rem  @st.fragment(run_every=1) - no CHI quay khi co trinh duyet dang mo tab do.
rem  Ngay 10/09/2026 mat gan het phien chieu vi khong ai mo cua so nay: may chu
rem  Streamlit van chay, worker van tick, nhung KHONG lenh nao duoc gui.
rem ============================================================================
echo.
echo [Buoc 4/5] Kiem tra app Streamlit (cong 8501)...
echo.
set APP_URL=http://localhost:8501
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$l = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue;" ^
  "if(-not $l){ Write-Host '  May chu Streamlit CHUA CHAY - hay chay Chay_App.bat truoc.' -ForegroundColor Red; exit 2 }" ^
  "$e = Get-NetTCPConnection -LocalPort 8501 -State Established -ErrorAction SilentlyContinue;" ^
  "if($e){ Write-Host ('  OK - da co ' + @($e).Count + ' trinh duyet mo app.') -ForegroundColor Green; exit 0 }" ^
  "Write-Host '  May chu song nhung KHONG trinh duyet nao mo app.' -ForegroundColor Yellow; exit 1"
if errorlevel 2 goto :thieu_server
if not errorlevel 1 goto :xong

echo.
echo [Buoc 5/5] Dang mo app bang Chrome...
start "" "%CHROME%" "%APP_URL%"
rem Cho trinh duyet ket noi websocket toi Streamlit roi kiem lai.
timeout /t 8 /nobreak >nul 2>&1 || ping -n 9 127.0.0.1 >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$e = Get-NetTCPConnection -LocalPort 8501 -State Established -ErrorAction SilentlyContinue;" ^
  "if($e){ Write-Host ('  OK - app da mo (' + @($e).Count + ' ket noi).') -ForegroundColor Green }" ^
  "else { Write-Host '  Van chua thay ket noi - mo tay ' -NoNewline; Write-Host $env:APP_URL -ForegroundColor Yellow }"
goto :xong

:thieu_server
echo.
echo ====================================================================
echo  THIEU MAY CHU APP. Chay Chay_App.bat (hoac shortcut "App CK"),
echo  doi app len roi chay lai file nay.
echo ====================================================================
goto :ket_thuc

:xong
echo.
echo ====================================================================
echo  CA HAI CUA SO DA SAN SANG:
echo    1) Chrome AutoTrade - SmartPro (cong 9222) : noi lenh THAT di ra
echo    2) App Streamlit    - tab PHAI SINH        : noi engine chay
echo.
echo  GIU NGUYEN CA HAI. Dong bat ky cai nao la bot ngung hoat dong:
echo    - Dong cua so 1 -^> khong dat duoc lenh that
echo    - Dong cua so 2 -^> engine ngung han, khong con tin hieu nao
echo.
echo  Nho bam sang tab PHAI SINH trong app va bat "Auto Refresh (1s)".
echo ====================================================================

:ket_thuc
echo.
pause
