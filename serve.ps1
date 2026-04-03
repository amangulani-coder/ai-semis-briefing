$port = 3000
$root = $PSScriptRoot
$listener = New-Object Net.HttpListener
$listener.Prefixes.Add("http://localhost:$port/")
$listener.Start()
Write-Host "Serving $root on http://localhost:$port/"
while ($listener.IsListening) {
    $ctx = $listener.GetContext()
    $path = $ctx.Request.Url.LocalPath
    if ($path -eq '/') { $path = '/index.html' }
    $file = Join-Path $root $path.TrimStart('/')
    if (Test-Path $file -PathType Leaf) {
        $ext = [IO.Path]::GetExtension($file).ToLower()
        $mime = switch ($ext) {
            '.html' { 'text/html; charset=utf-8' }
            '.js'   { 'application/javascript' }
            '.css'  { 'text/css' }
            '.json' { 'application/json' }
            '.png'  { 'image/png' }
            '.ico'  { 'image/x-icon' }
            default { 'application/octet-stream' }
        }
        $bytes = [IO.File]::ReadAllBytes($file)
        $ctx.Response.ContentType = $mime
        $ctx.Response.ContentLength64 = $bytes.Length
        $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    } else {
        $ctx.Response.StatusCode = 404
    }
    $ctx.Response.Close()
}
