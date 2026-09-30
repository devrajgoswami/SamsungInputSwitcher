<#
.SYNOPSIS
    Checks whether the attached monitor supports DDC/CI.

.DESCRIPTION
    DDC/CI is the standard way to change a monitor's input (VCP code 0x60) and
    is what tools such as ControlMyMonitor and ddcutil use. Samsung's Tizen
    based Smart Monitors, including the M70D, do not implement it - every
    request fails with an I2C transmit error. This script documents and
    re-verifies that, and is the reason this project uses the network API
    instead.

    A working monitor prints DdcResponds = True and a current input value.

.EXAMPLE
    .\tools\Test-DdcSupport.ps1
#>
[CmdletBinding()]
param(
    [int] $Attempts = 3
)

if (-not ('Ddc.Native' -as [type])) {
Add-Type -Language CSharp @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace Ddc
{
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct PHYSICAL_MONITOR
    {
        public IntPtr hPhysicalMonitor;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
        public string szPhysicalMonitorDescription;
    }

    public static class Native
    {
        public delegate bool MonitorEnumProc(IntPtr hMonitor, IntPtr hdc, IntPtr lprc, IntPtr data);

        [DllImport("user32.dll")]
        public static extern bool EnumDisplayMonitors(IntPtr hdc, IntPtr clip, MonitorEnumProc proc, IntPtr data);

        [DllImport("dxva2.dll", SetLastError = true)]
        public static extern bool GetNumberOfPhysicalMonitorsFromHMONITOR(IntPtr hMonitor, ref uint count);

        [DllImport("dxva2.dll", SetLastError = true)]
        public static extern bool GetPhysicalMonitorsFromHMONITOR(IntPtr hMonitor, uint size, [Out] PHYSICAL_MONITOR[] arr);

        [DllImport("dxva2.dll", SetLastError = true)]
        public static extern bool DestroyPhysicalMonitor(IntPtr hMonitor);

        [DllImport("dxva2.dll", SetLastError = true)]
        public static extern bool GetVCPFeatureAndVCPFeatureReply(IntPtr hMonitor, byte code, IntPtr type, ref uint current, ref uint max);

        [DllImport("dxva2.dll", SetLastError = true)]
        public static extern bool SetVCPFeature(IntPtr hMonitor, byte code, uint value);

        public static List<PHYSICAL_MONITOR> GetAll()
        {
            var result = new List<PHYSICAL_MONITOR>();
            MonitorEnumProc cb = delegate(IntPtr hMon, IntPtr hdc, IntPtr lprc, IntPtr data)
            {
                uint count = 0;
                if (GetNumberOfPhysicalMonitorsFromHMONITOR(hMon, ref count) && count > 0)
                {
                    var arr = new PHYSICAL_MONITOR[count];
                    if (GetPhysicalMonitorsFromHMONITOR(hMon, count, arr))
                        result.AddRange(arr);
                }
                return true;
            };
            EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, cb, IntPtr.Zero);
            return result;
        }
    }
}
'@
}

$monitors = [Ddc.Native]::GetAll()
if (-not $monitors) { Write-Warning 'No physical monitors found.'; return }

try {
    for ($i = 0; $i -lt $monitors.Count; $i++) {
        $h = $monitors[$i].hPhysicalMonitor
        $responded = $false
        $value = $null
        $err = 0

        for ($a = 0; $a -lt $Attempts; $a++) {
            $cur = 0; $max = 0
            if ([Ddc.Native]::GetVCPFeatureAndVCPFeatureReply($h, 0x60, [IntPtr]::Zero, [ref]$cur, [ref]$max)) {
                $responded = $true
                $value = $cur
                break
            }
            $err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            Start-Sleep -Milliseconds 300
        }

        [pscustomobject]@{
            Index         = $i
            Description   = $monitors[$i].szPhysicalMonitorDescription
            DdcResponds   = $responded
            InputValue    = if ($responded) { '0x{0:X2}' -f $value } else { $null }
            Win32Error    = if ($responded) { 0 } else { '0x{0:X8}' -f $err }
        }
    }
}
finally {
    foreach ($m in $monitors) { [void][Ddc.Native]::DestroyPhysicalMonitor($m.hPhysicalMonitor) }
}
