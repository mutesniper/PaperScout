import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pytest
from .serve import handler_for, IDENTITY


def test_service_validates_state_and_reports_context_limit():
    called=[]
    def predict(state):
        called.append(state)
        if state.goal=='long':
            raise ValueError('too many tokens')
        return '{"route":"search"}'
    server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(predict))
    worker=threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def post(value):
        request=Request(base+'/route',data=json.dumps(value).encode(),headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=5) as response:
            return json.load(response)
    try:
        with urlopen(base+'/health') as response:
            assert json.load(response)=={'ready':True,**IDENTITY}
        assert post({'goal':'找论文'})['raw']=='{"route":"search"}'
        with pytest.raises(HTTPError) as error:
            post({'goal':'找论文','extra':'do not accept'})
        assert error.value.code==422 and len(called)==1
        with pytest.raises(HTTPError) as error:
            post({'goal':'long'})
        assert error.value.code==422
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
