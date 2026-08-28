@echo off
rem Chrome danh rieng cho dat lenh tu dong — profile RIENG + cong debug 9222.
rem 1) Chay file nay  2) Dang nhap SmartPro + nhap PIN trong cua so hien ra
rem 3) DE NGUYEN cua so do — bot noi vao qua cong 9222, khong dung cookie noi khac.
rem Profile rieng nen viec ban dong/mo trinh duyet chinh KHONG anh huong.

set PROFILE=%LOCALAPPDATA%\VNInvest\ChromeAutoTrade
if not exist "%PROFILE%" mkdir "%PROFILE%"

start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --user-data-dir="%PROFILE%" ^
  --no-first-run --no-default-browser-check ^
  "https://smartpro.vps.com.vn/v1/"
