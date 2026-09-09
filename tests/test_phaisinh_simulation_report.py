import pytest
from vn_invest import phaisinh_tab as ps


@pytest.mark.parametrize('side,exit_price', [('LONG',2005.),('LONG',1995.),('SHORT',1995.)])
def test_simulation_is_not_presented_as_account_result(side,exit_price):
    pos=dict(side=side,entry=2000.,tid=1,bars=3)
    message=ps._simulation_close_message(pos,exit_price,'SL <test>')
    assert 'MÔ PHỎNG' in message
    assert 'Không phải kết quả khớp lệnh hay lãi/lỗ tài khoản VPS' in message
    assert 'phí giả định' in message and 'điểm/HĐ' in message
    assert 'THẮNG' not in message and 'THUA' not in message
    assert 'VND/HĐ' not in message
    assert 'SL &lt;test&gt;' in message
    assert 'bị từ chối' in message
