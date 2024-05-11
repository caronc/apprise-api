# -*- coding: utf-8 -*-
#
# Copyright (C) 2019 Chris Caron <lead2gold@gmail.com>
# All rights reserved.
#
# This code is licensed under the MIT License.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files(the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and / or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions :
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
from django.urls import re_path
from . import views

urlpatterns = [
    re_path(
        r'^$',
        views.WelcomeView.as_view(), name='welcome'),
    re_path(
        r'^status/?$',
        views.HealthCheckView.as_view(), name='health'),
    re_path(
        r'^details/?$',
        views.DetailsView.as_view(), name='details'),
    re_path(
        r'^cfg/(?P<key>[\w_-]{1,128})/?$',
        views.ConfigView.as_view(), name='config'),
    re_path(
        r'^add/(?P<key>[\w_-]{1,128})/?$',
        views.AddView.as_view(), name='add'),
    re_path(
        r'^del/(?P<key>[\w_-]{1,128})/?$',
        views.DelView.as_view(), name='del'),
    re_path(
        r'^get/(?P<key>[\w_-]{1,128})/?$',
        views.GetView.as_view(), name='get'),
    re_path(
        r'^notify/(?P<key>[\w_-]{1,128})/?$',
        views.NotifyView.as_view(), name='notify'),
    re_path(
        r'^notify/?$',
        views.StatelessNotifyView.as_view(), name='s_notify'),
    re_path(
        r'^json/urls/(?P<key>[\w_-]{1,128})/?$',
        views.JsonUrlView.as_view(), name='json_urls'),
]
