use strict;
use HTTP::Daemon;
use HTTP::Status;
use File::Basename;

my $port = 3000;
my $root = dirname(__FILE__);

my $d = HTTP::Daemon->new(LocalPort => $port, ReuseAddr => 1)
    or die "Cannot start server: $!";

print "Serving $root on " . $d->url . "\n";

my %mime = (
    '.html' => 'text/html; charset=utf-8',
    '.js'   => 'application/javascript',
    '.css'  => 'text/css',
    '.json' => 'application/json',
    '.png'  => 'image/png',
    '.ico'  => 'image/x-icon',
);

while (my $c = $d->accept) {
    while (my $r = $c->get_request) {
        my $path = $r->url->path;
        $path = '/index.html' if $path eq '/';
        $path =~ s|^/||;
        my $file = "$root/$path";
        if (-f $file) {
            my ($ext) = $file =~ /(\.[^.]+)$/;
            my $type = $mime{lc($ext)} // 'application/octet-stream';
            open my $fh, '<:raw', $file or next;
            local $/;
            my $body = <$fh>;
            close $fh;
            my $res = HTTP::Response->new(200);
            $res->content_type($type);
            $res->header('Cache-Control', 'no-store');
            $res->content($body);
            $c->send_response($res);
        } else {
            $c->send_error(RC_NOT_FOUND);
        }
    }
    $c->close;
}
