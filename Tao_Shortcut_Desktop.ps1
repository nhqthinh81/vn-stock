# Tao lai 3 shortcut Desktop cua VN Invest (luu tru cau hinh, 15/09/2026).
#   powershell -ExecutionPolicy Bypass -File Tao_Shortcut_Desktop.ps1
#   powershell -ExecutionPolicy Bypass -File Tao_Shortcut_Desktop.ps1 -Destination C:\tmp\thu
#
# "App CK" PHAI tro vao Chay_App.bat, KHONG tro thang streamlit.exe: ban cu goi
# thang streamlit.exe nen bo qua chot chan server thu hai trong Chay_App.bat ->
# moi lan bam mo them 1 server (8501, 8502, 8503 cung chay, 15/09/2026).
param([string]$Destination = [Environment]::GetFolderPath('Desktop'))

$repo = $PSScriptRoot
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$items = @(
    @{ Name = 'App CK'; Target = 'Chay_App.bat'; Icon = ''
       Desc = 'Mo dashboard VN Invest (1 server duy nhat, cong 8501) + Chrome AutoTrade neu chua chay' },
    @{ Name = 'Chrome AutoTrade VPS'; Target = 'Chay_Chrome_AutoTrade.bat'; Icon = "$chrome,0"
       Desc = 'Mo Chrome rieng cho dat lenh tu dong VPS SmartPro (profile rieng + cong debug 9333)' },
    @{ Name = 'Kiem Tra Chrome AutoTrade'; Target = 'Kiem_Tra_Chrome_AutoTrade.bat'; Icon = ''
       Desc = 'Kiem tra cong 9333 co dang mo dung SmartPro khong - chi doc, chay truoc moi phien' }
)

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$shell = New-Object -ComObject WScript.Shell
foreach ($it in $items) {
    $target = Join-Path $repo $it.Target
    if (-not (Test-Path $target)) { Write-Warning "Khong thay $target - bo qua"; continue }
    $lnk = $shell.CreateShortcut((Join-Path $Destination ($it.Name + '.lnk')))
    $lnk.TargetPath = $target
    $lnk.Arguments = ''
    $lnk.WorkingDirectory = $repo
    $lnk.WindowStyle = 1
    $lnk.Description = $it.Desc
    if ($it.Icon -and (Test-Path $chrome)) { $lnk.IconLocation = $it.Icon }
    $lnk.Save()
    Write-Output ("OK  {0}.lnk -> {1}" -f $it.Name, $target)
}
