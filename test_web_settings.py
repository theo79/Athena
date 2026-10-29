"""Authenticated settings HTTP tests; temporary data and mocked inference only."""
from contextlib import contextmanager
import http.client
import json
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from provider_configuration import ProviderConfigurationService
from test_conversation import final
import web_settings

TOKEN='synthetic-owner-token-for-tests-only-123456'


@contextmanager
def running(service, **kwargs):
    server=web_settings.SettingsServer(service,owner_token=TOKEN,port=0,network_reader=lambda:{
        'hostname':'test-host','addresses':[],'reachability':'Not tested.'},**kwargs)
    thread=Thread(target=server.serve_forever,kwargs={'poll_interval':0.01})
    thread.start()
    try: yield server
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
        assert not thread.is_alive()


@pytest.fixture
def setup(tmp_path):
    factory=Mock(return_value=SimpleNamespace(generate=Mock(return_value=final('ok')),
                                             list_models=Mock(return_value=['selected'])))
    env={'OPENAI_API_KEY':'synthetic-private-api-key'}
    service=ProviderConfigurationService(tmp_path/'config # μ.json',environ=env,provider_factory=factory)
    return service,factory


def request(server,path='/api/state',payload=None,headers=None,auth=True,raw=None):
    connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
    supplied={'Authorization':'Bearer '+TOKEN} if auth else {}
    if payload is not None or raw is not None:
        supplied.update({'Origin':server.url,'Content-Type':'application/json','X-Athena-CSRF':server._csrf})
    supplied.update(headers or {})
    body=json.dumps(payload) if payload is not None else raw
    connection.request('POST' if body is not None else 'GET',path,body=body,headers=supplied)
    response=connection.getresponse()
    data=response.read()
    result=(response.status,dict(response.getheaders()),data)
    connection.close()
    return result


def selection(provider='local',model='selected',endpoint='http://localhost:11434'):
    return {'selection':{'provider':provider,'model':model,'endpoint':endpoint if provider=='local' else None,
                         'credential_env':None if provider=='local' else 'OPENAI_API_KEY'},
            'allow_network':True,'acknowledge_cloud':provider!='local'}


def test_start_stop_loopback_no_provider_requests(setup):
    service,factory=setup
    with running(service) as server:
        assert server.server_address[0]=='127.0.0.1'
        assert request(server)[0]==200
        factory.assert_not_called()
        assert not service.path.exists()


@pytest.mark.parametrize('auth,headers',[(False,{}),(True,{'Authorization':'Bearer wrong'})])
def test_owner_authentication(setup,auth,headers):
    with running(setup[0]) as server:
        assert request(server,auth=auth,headers=headers)[0]==401
        assert request(server,'/api/switch',selection(),auth=auth,headers=headers)[0]==401
    setup[1].assert_not_called()


def test_login_shell_and_assets_contain_no_credentials(setup):
    with running(setup[0]) as server:
        for path,content in [('/',b'Your intelligence'),('/assets/settings.css',b'mode-grid'),
                             ('/assets/settings.js',b'Test')]:
            status,headers,body=request(server,path,auth=False)
            assert status==200 and content.lower() in body.lower()
            assert TOKEN.encode() not in body and b'synthetic-private-api-key' not in body
            assert headers['Cache-Control']=='no-store' and 'frame-ancestors' in headers['Content-Security-Policy']
        assert request(server,'/provider_configuration.py')[0]==404


@pytest.mark.parametrize('headers',[{'Host':'evil.example:8765'},{'Origin':'http://evil.example'},
    {'Origin':'null'},{'X-Athena-CSRF':'wrong'}])
def test_request_protection(setup,headers):
    with running(setup[0]) as server:
        assert request(server,'/api/switch',selection(),headers=headers)[0]==403
    setup[1].assert_not_called()


def test_post_requires_origin_and_csrf(setup):
    with running(setup[0]) as server:
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port)
        body=json.dumps(selection())
        connection.request('POST','/api/switch',body,{'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json'})
        assert connection.getresponse().status==403
        connection.close()


def test_test_only_and_switch_share_service(setup):
    service,factory=setup
    with running(service) as server:
        assert request(server,'/api/test',selection())[0]==200
        assert not service.path.exists()
        assert request(server,'/api/switch',selection())[0]==200
        assert json.loads(service.path.read_text())['model_provider']=='local'
        state=json.loads(request(server)[2])
        assert state['last_check']['ok'] and state['last_check']['elapsed_ms']>=0
        assert 'next start' in state['activation']
        assert state['configuration']['model']=='selected'
    assert factory.return_value.generate.call_count==2


@pytest.mark.parametrize('provider',['openai','gemini','openrouter'])
def test_cloud_switch_and_ack(setup,provider):
    service,factory=setup
    service.environ.update(GEMINI_API_KEY='synthetic-private-api-key',OPENROUTER_API_KEY='synthetic-private-api-key')
    with running(service) as server:
        payload=selection(provider)
        payload['selection']['credential_env']={'openai':'OPENAI_API_KEY','gemini':'GEMINI_API_KEY','openrouter':'OPENROUTER_API_KEY'}[provider]
        payload['acknowledge_cloud']=False
        assert request(server,'/api/switch',payload)[0]==422
        factory.assert_not_called()
        payload['acknowledge_cloud']=True
        assert request(server,'/api/switch',payload)[0]==200
        assert service.read().provider==provider
        assert b'synthetic-private-api-key' not in request(server)[2]


@pytest.mark.parametrize('code',['unreachable','invalid_credentials','timeout','model_unavailable'])
def test_failed_switch_preserves_previous(setup,code):
    from models import ModelFailure
    service,factory=setup
    service.path.write_text('{"model_provider":"local","model_name":"previous","ollama_base_url":"http://localhost:11434","custom":true}')
    before=service.path.read_bytes()
    factory.return_value.generate.return_value=ModelFailure('private provider response',code)
    with running(service) as server:
        status,_,body=request(server,'/api/switch',selection('openai'))
        assert status==422 and json.loads(body)['code']==code
        assert b'private provider response' not in body
    assert service.path.read_bytes()==before


def test_no_arbitrary_endpoint_or_credential_values(setup):
    with running(setup[0]) as server:
        assert request(server,'/api/test',selection(endpoint='http://192.168.1.3:8080'))[0]==403
        payload=selection('openai')
        payload['credential']='secret-should-never-be-accepted'
        assert request(server,'/api/switch',payload)[0]==400
    setup[1].assert_not_called()


def test_owner_enabled_endpoint_and_discovery(setup):
    with running(setup[0],local_endpoints=['http://127.0.0.1:12345']) as server:
        status,_,body=request(server,'/api/models',selection(endpoint='http://127.0.0.1:12345'))
        assert status==200 and json.loads(body)['models']==['selected']


@pytest.mark.parametrize('raw,headers,expected', [('{}',{},400),('{',{},400),
    ('x'*8193,{},413),('{}',{'Content-Type':'text/plain'},415)])
def test_request_validation(setup,raw,headers,expected):
    with running(setup[0]) as server:
        assert request(server,'/api/test',raw=raw,headers=headers)[0]==expected


def test_request_read_timeout_is_bounded(setup,monkeypatch):
    monkeypatch.setattr(web_settings,'READ_TIMEOUT',0.05)
    with running(setup[0]) as server:
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
        connection.request('POST','/api/test',body=b'{',headers={'Authorization':'Bearer '+TOKEN,
            'Origin':server.url,'X-Athena-CSRF':server._csrf,'Content-Length':'100','Content-Type':'application/json'})
        assert connection.getresponse().status==408
        connection.close()


def test_lan_rejected_without_explicit_permission_and_tls(setup):
    for kwargs in ({},{'allow_lan':True},{'tls_context':Mock()}):
        with pytest.raises(ValueError):
            web_settings.SettingsServer(setup[0],owner_token=TOKEN,bind='192.168.1.2',**kwargs)


def test_explicit_lan_uses_tls_context(setup,monkeypatch):
    def fake_bind(self,address,handler):
        self.server_port=8765
        self.server_address=address
        self.socket=Mock()
    monkeypatch.setattr(web_settings.HTTPServer,'__init__',fake_bind)
    context=Mock()
    server=web_settings.SettingsServer(setup[0],owner_token=TOKEN,bind='192.168.1.2',
                                     allow_lan=True,tls_context=context)
    assert server.scheme=='https'
    context.wrap_socket.assert_called_once()


def test_empty_network_and_unchanged_memory(setup,tmp_path):
    service,factory=setup
    memory=tmp_path/'memory.json'
    memory.write_text('[{"text":"unchanged"}]')
    before=memory.read_bytes()
    with running(service) as server:
        state=json.loads(request(server)[2])
        assert state['network']['addresses']==[] and state['last_check'] is None
        assert 'not monitored' in state['runtime']
        assert request(server,'/api/switch',selection())[0]==200
    assert memory.read_bytes()==before


def test_unexpected_error_is_redacted(setup):
    service,factory=setup
    service.test=Mock(side_effect=RuntimeError('private api secret payload'))
    with running(service) as server:
        status,_,body=request(server,'/api/test',selection())
        assert status==500 and b'private api secret' not in body


def test_restart_after_web_switch_ignores_stale_environment(setup):
    service,factory=setup
    with running(service) as server:
        assert request(server,'/api/switch',selection())[0]==200
    fresh=ProviderConfigurationService(service.path,environ={'MODEL_PROVIDER':'openai','MODEL_NAME':'old'})
    with running(fresh) as server:
        data=json.loads(request(server)[2])
        assert data['configuration']['provider']=='local'
        assert 'MODEL_PROVIDER' in data['selection_status']['conflicts']


@pytest.mark.parametrize('port',[-1,65536,True])
def test_invalid_port_rejected(setup,port):
    with pytest.raises(ValueError):
        web_settings.SettingsServer(setup[0],owner_token=TOKEN,port=port)


def test_server_survives_malformed_configuration(setup):
    service,factory=setup
    service.path.write_text('{"model_provider":["invalid"]}')
    with running(service) as server:
        status,_,body=request(server)
        assert status==200 and json.loads(body)['configuration'] is None
    factory.assert_not_called()
