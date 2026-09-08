# Kiem tra nhanh: cong debug 9222 co dang mo dung trang SmartPro khong.
# Dung truoc moi phien giao dich de tranh lap lai su co 08/09/2026 (Chrome
# giu cong 9222 nhung lai dang o SmartOne, khong phai SmartPro, suot 10 ngay
# ma khong ai de y vi khong co canh bao nao).
#
# Chi doc qua HTTP endpoint cua Chrome DevTools (http://127.0.0.1:9222/json)
# KHONG dung Playwright, KHONG dam vao trang, an toan chay bat ky luc nao
# ke ca dang co lenh that dang mo (chi doc danh sach tab, khong evaluate).

$port = 9222
$expectedHost = "smartpro.vps.com.vn"

Write-Host "=== Kiem tra Chrome AutoTrade (cong $port) ===" -ForegroundColor Cyan

try {
    $tabs = Invoke-RestMethod -Uri "http://127.0.0.1:$port/json" -TimeoutSec 3
} catch {
    Write-Host "KHONG mo duoc cong $port - Chrome AutoTrade chua chay hoac da dong." -ForegroundColor Red
    Write-Host "Chay Chay_Chrome_AutoTrade.bat (hoac shortcut Chrome AutoTrade VPS tren Desktop) roi dang nhap SmartPro." -ForegroundColor Yellow
    exit 1
}

$pageTabs = $tabs | Where-Object { $_.type -eq "page" }
if (-not $pageTabs) {
    Write-Host "Cong $port dang mo nhung KHONG thay tab nao - kiem tra lai cua so Chrome do." -ForegroundColor Red
    exit 1
}

$smartProTabs = $pageTabs | Where-Object { $_.url -like "*$expectedHost*" }

if ($smartProTabs) {
    Write-Host "OK - tim thay tab SmartPro dung cong $port :" -ForegroundColor Green
    foreach ($t in $smartProTabs) {
        Write-Host ("  - " + $t.title) -ForegroundColor Green
        Write-Host ("    " + $t.url)
    }
    if (@($smartProTabs).Count -gt 1) {
        Write-Host "Luu y: co nhieu hon 1 tab SmartPro - dam bao chi co 1 de tranh nham lan." -ForegroundColor Yellow
    }
    exit 0
}

Write-Host "CANH BAO - cong $port dang mo nhung KHONG co tab nao la SmartPro." -ForegroundColor Red
Write-Host "Cac tab dang mo trong cua so nay:" -ForegroundColor Yellow
foreach ($t in $pageTabs) {
    Write-Host ("  - " + $t.title)
    Write-Host ("    " + $t.url)
}
Write-Host ""
Write-Host "Dung dung Chrome nay cho auto-trade - dieu huong ve https://smartpro.vps.com.vn/v1/" -ForegroundColor Yellow
Write-Host "hoac dong cua so nay roi chay lai Chay_Chrome_AutoTrade.bat" -ForegroundColor Yellow
exit 2
