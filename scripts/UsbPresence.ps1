<#
.SYNOPSIS
    Fast, dependency-free check for whether a USB device is currently present.

.DESCRIPTION
    Dot-source this file to get Test-UsbDevicePresent and Get-PresentUsbIds.

    Get-PnpDevice issues a full CIM query and takes on the order of a second,
    which is far too slow to poll. CM_Get_Device_ID_List from cfgmgr32 returns
    the list of present device IDs in a few milliseconds, so the watcher can
    poll frequently without measurable cost. That also removes any dependence
    on WMI device-change notifications being delivered.
#>

if (-not ('UsbPresence.Native' -as [type])) {
Add-Type -Language CSharp @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace UsbPresence
{
    public static class Native
    {
        private const uint CM_GETIDLIST_FILTER_ENUMERATOR = 0x00000001;
        private const uint CM_GETIDLIST_FILTER_PRESENT    = 0x00000100;

        [DllImport("cfgmgr32.dll", CharSet = CharSet.Unicode)]
        private static extern int CM_Get_Device_ID_List_SizeW(out uint pulLen, string pszFilter, uint ulFlags);

        [DllImport("cfgmgr32.dll", CharSet = CharSet.Unicode)]
        private static extern int CM_Get_Device_ID_ListW(string pszFilter, char[] buffer, uint bufferLen, uint ulFlags);

        /// <summary>Device instance IDs of all present devices under the given enumerator (e.g. "USB").</summary>
        public static string[] GetPresentIds(string enumerator)
        {
            uint flags = CM_GETIDLIST_FILTER_PRESENT;
            if (!string.IsNullOrEmpty(enumerator))
                flags |= CM_GETIDLIST_FILTER_ENUMERATOR;

            uint len;
            int cr = CM_Get_Device_ID_List_SizeW(out len, enumerator, flags);
            if (cr != 0 || len == 0) return new string[0];

            var buffer = new char[len];
            cr = CM_Get_Device_ID_ListW(enumerator, buffer, len, flags);
            if (cr != 0) return new string[0];

            // Result is a MULTI_SZ: NUL-separated, double-NUL terminated.
            var ids = new List<string>();
            int start = 0;
            for (int i = 0; i < buffer.Length; i++)
            {
                if (buffer[i] != '\0') continue;
                if (i > start) ids.Add(new string(buffer, start, i - start));
                start = i + 1;
                if (i + 1 < buffer.Length && buffer[i + 1] == '\0') break;
            }
            return ids.ToArray();
        }
    }
}
'@
}

function Get-PresentUsbIds {
    [CmdletBinding()]
    param([string] $Enumerator = 'USB')
    [UsbPresence.Native]::GetPresentIds($Enumerator)
}

function Test-UsbDevicePresent {
    <#
    .SYNOPSIS
        True when a present device instance ID starts with $InstanceIdPrefix.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $InstanceIdPrefix,
        [string] $Enumerator = 'USB'
    )

    $prefix = $InstanceIdPrefix
    # CM returns IDs without the leading enumerator duplication issues, but the
    # caller may pass either "USB\VID_x&PID_y" or "VID_x&PID_y".
    foreach ($id in [UsbPresence.Native]::GetPresentIds($Enumerator)) {
        if ($id.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}
