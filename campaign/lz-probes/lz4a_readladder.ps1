# LZ4a — Windows unbuffered sequential-read ladder (no fio, no install).
# Decides whether porting PR ggml-org/llama.cpp#26014 is worth it: gate = >=6 GB/s
# attainable at some feasible chunk size. QD1 only — if QD1 tops out low, that IS the
# edge and the next probe designs queue-depth tests.
param(
    [string]$File = "E:\work\battlemage\models\qwen38\Qwen3.8-27B-Q4_K_M.gguf",
    [long]$BytesPerPoint = 8GB,
    [string]$Receipts = "E:\work\battlemage\lz-probes\lz-receipts.jsonl"
)
$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @"
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
public static class RawRead {
    const uint GENERIC_READ = 0x80000000;
    const uint OPEN_EXISTING = 3;
    const uint FILE_FLAG_NO_BUFFERING = 0x20000000;
    const uint FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000;
    [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
    static extern IntPtr CreateFileW(string name, uint access, uint share, IntPtr sec,
        uint disp, uint flags, IntPtr tmpl);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool ReadFile(IntPtr h, IntPtr buf, uint n, out uint read, IntPtr ovl);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern IntPtr VirtualAlloc(IntPtr addr, UIntPtr size, uint type, uint prot);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool VirtualFree(IntPtr addr, UIntPtr size, uint type);
    // Returns GB/s reading `total` bytes in `chunk`-sized unbuffered sequential reads.
    public static double Ladder(string path, long chunk, long total, bool unbuffered) {
        uint flags = FILE_FLAG_SEQUENTIAL_SCAN | (unbuffered ? FILE_FLAG_NO_BUFFERING : 0u);
        IntPtr h = CreateFileW(path, GENERIC_READ, 1, IntPtr.Zero, OPEN_EXISTING, flags, IntPtr.Zero);
        if (h == (IntPtr)(-1)) throw new Exception("CreateFile failed: " + Marshal.GetLastWin32Error());
        IntPtr buf = VirtualAlloc(IntPtr.Zero, (UIntPtr)chunk, 0x3000 /*MEM_COMMIT|RESERVE*/, 4 /*PAGE_READWRITE*/);
        if (buf == IntPtr.Zero) { CloseHandle(h); throw new Exception("VirtualAlloc failed"); }
        long done = 0; uint got;
        var sw = Stopwatch.StartNew();
        while (done < total) {
            if (!ReadFile(h, buf, (uint)chunk, out got, IntPtr.Zero) || got == 0)
                break; // EOF or error ends the pass
            done += got;
        }
        sw.Stop();
        VirtualFree(buf, UIntPtr.Zero, 0x8000 /*MEM_RELEASE*/);
        CloseHandle(h);
        return done / sw.Elapsed.TotalSeconds / 1e9;
    }
}
"@

function Add-Receipt($row) {
    $row | ConvertTo-Json -Compress | Add-Content -Encoding utf8 -Path $Receipts
}

$f = Get-Item $File
"file: $($f.FullName)  size: $([math]::Round($f.Length/1GB,2)) GB  points read $([math]::Round($BytesPerPoint/1GB,1)) GB each"

foreach ($chunkMiB in 1, 16, 64, 256, 1024) {
    $chunk = [long]$chunkMiB * 1MB
    $gbps = [math]::Round([RawRead]::Ladder($f.FullName, $chunk, $BytesPerPoint, $true), 2)
    "unbuffered  bs=${chunkMiB}MiB  $gbps GB/s"
    Add-Receipt @{ ts = (Get-Date).ToString('o'); probe = 'LZ4a'; variant = "unbuffered-bs${chunkMiB}M";
                   file = $f.Name; gbps = $gbps }
}
# buffered baseline LAST (pollutes standby cache with up to $BytesPerPoint)
$gbpsBuf = [math]::Round([RawRead]::Ladder($f.FullName, 64MB, $BytesPerPoint, $false), 2)
"buffered    bs=64MiB   $gbpsBuf GB/s  (page-cache polluting; run last)"
Add-Receipt @{ ts = (Get-Date).ToString('o'); probe = 'LZ4a'; variant = 'buffered-bs64M';
               file = $f.Name; gbps = $gbpsBuf }
