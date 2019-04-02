from mock import MagicMock

from zmon_worker_monitor.builtins.plugins.whois_ import WhoisWrapper


def test_whois(monkeypatch):
    m_whois = MagicMock()
    m_whois.whois.return_value = {'expiration_date': 123}
    monkeypatch.setattr('zmon_worker_monitor.builtins.plugins.whois_.whois', m_whois)

    whois = WhoisWrapper(host='example.org')
    res = whois.check()

    assert res == {'expiration_date': 123}

    m_whois.whois.assert_called_with('example.org')
