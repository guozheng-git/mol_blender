import pytest
from blender_render import uploads
from blender_render.jobs import JobError


@pytest.mark.parametrize('url',['http://example.org/a','file:///tmp/a','https://name:secret@example.org/a','https://example.org:8080/a','https://example.org/a#fragment'])
def test_rejects_unsafe_url_before_network(url, monkeypatch):
    monkeypatch.setattr(uploads.socket,'getaddrinfo',lambda *a,**k:pytest.fail('No DNS should be attempted'))
    with pytest.raises(JobError): uploads.download_file(url)


@pytest.mark.parametrize('ip',['127.0.0.1','10.0.0.1','169.254.169.254','::1','fe80::1','::ffff:127.0.0.1'])
def test_rejects_private_and_metadata_networks(ip, monkeypatch):
    monkeypatch.setattr(uploads.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',(ip,443))])
    with pytest.raises(JobError): uploads.resolve_public('upload.example.org')


def test_redirect_destination_is_revalidated(monkeypatch):
    lookups=[]
    def resolve(host):
        lookups.append(host)
        if host == 'private.example.org': raise JobError('Private address')
        return ['8.8.8.8']
    monkeypatch.setattr(uploads,'resolve_public',resolve)
    class Response:
        status=302
        def getheader(self,*args): return 'https://private.example.org/file'
    class Connection:
        def __init__(self,host,address): assert (host,address)==('public.example.org','8.8.8.8')
        def request(self,*args,**kwargs): assert 'Authorization' not in kwargs['headers']
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(uploads,'PinnedHTTPS',Connection)
    with pytest.raises(JobError): uploads.download_file('https://public.example.org/file')
    assert lookups == ['public.example.org','private.example.org']
