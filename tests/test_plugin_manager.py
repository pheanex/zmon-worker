#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import traceback
import importlib
import time
import pytest

from mock import patch

from zmon_worker_monitor.adapters.ifunctionfactory_plugin import IFunctionFactoryPlugin
from zmon_worker_monitor import plugin_manager
from .plugins.icolor_base_plugin import IColorPlugin
from .plugins.itemperature_base_plugin import ITemperaturePlugin


def simple_plugin_dir_abs_path(*suffixes):
    return os.path.abspath(os.path.join(os.path.dirname(__file__), 'plugins/simple_plugins', *suffixes))


def broken_plugin_dir_abs_path(*suffixes):
    return os.path.abspath(os.path.join(os.path.dirname(__file__), 'plugins/broken_plugins', *suffixes))


def extras_plugin_dir_abs_path(*suffixes):
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '../zmon_worker_extras/check_plugins', *suffixes))


def test_load_plugins_twice():
    """
    Test that exception is raised if you collect plugins more than once
    """
    # reload the plugin
    importlib.reload(plugin_manager)

    plugin_manager.init_plugin_manager()  # init plugin manager

    plugin_manager.collect_plugins(load_builtins=True, load_env=False, additional_dirs=None)

    with pytest.raises(plugin_manager.PluginFatalError):
        plugin_manager.collect_plugins(load_builtins=True, load_env=False, additional_dirs=None)


def test_load_builtin_plugins():
    """
    Test that city plugin can be fully loaded
    """
    # reload the plugin
    importlib.reload(plugin_manager)

    plugin_manager.init_plugin_manager()  # init the plugin manager

    # collect only builtin plugins
    plugin_manager.collect_plugins(load_builtins=True, load_env=False, additional_dirs=None)

    # city plugin is a builtin and is in category entity
    plugin_name = 'http'
    plugin_category = 'Function'

    assert plugin_name in plugin_manager.get_all_plugin_names(), 'http plugin name must be found'

    http_plugin = plugin_manager.get_plugin_by_name(plugin_name, plugin_category)

    assert http_plugin is not None, 'http plugin must be under category Function'
    assert http_plugin.name == plugin_name, 'check plugin name field'

    # check city plugin object
    assert hasattr(http_plugin, 'plugin_object'), 'http.plugin_object exists'
    assert http_plugin.plugin_object is not None, 'http.plugin_object is not None'
    assert isinstance(plugin_manager.get_plugin_obj_by_name(plugin_name, plugin_category), IFunctionFactoryPlugin)

    # check that city plugin object is activated
    assert http_plugin.is_activated is True
    assert http_plugin.plugin_object.is_activated == http_plugin.is_activated


@patch.dict(os.environ, {'ZMON_PLUGINS': simple_plugin_dir_abs_path()})
def test_load_plugins_several_categories():
    """
    Test is we can load and correctly locate plugins from several categories
    First it explores folders from ZMON_PLUGINS env_var, and then from additional_dirs
    """
    for test_load_from in ('env_var', 'additional_folders'):

        # reload the plugin
        importlib.reload(plugin_manager)

        # Lets create a category filter that includes our builtin plugin type and 2 types we defines for our tests
        category_filter = {
            'Function': IFunctionFactoryPlugin,
            'Color': IColorPlugin,
            'Temperature': ITemperaturePlugin,
        }

        if test_load_from == 'env_var':
            # init the plugin manager
            plugin_manager.init_plugin_manager(category_filter=category_filter)

            # collect plugins builtin and explore env_var: ZMON_PLUGINS="/.../tests/plugins/simple_plugins"
            plugin_manager.collect_plugins(load_builtins=True, load_env=True, additional_dirs=None)

        elif test_load_from == 'additional_folders':
            # init the plugin manager
            plugin_manager.init_plugin_manager(category_filter=category_filter)

            test_plugin_dir = simple_plugin_dir_abs_path()

            # collect plugins builtin and explore  additional_dirs: /.../tests/plugins/simple_plugins
            plugin_manager.collect_plugins(load_builtins=True, load_env=False, additional_dirs=[test_plugin_dir])

        # check categories

        all_categories = plugin_manager.get_all_categories()
        seen_categories = plugin_manager.get_loaded_plugins_categories()

        assert set(all_categories) == set(category_filter.keys()), 'All defined categories are stored'
        assert len(seen_categories) >= 2 and set(seen_categories).issubset(set(all_categories))

        # check known test plugins are loaded

        known_plugin_names = ['http', 'color_spain', 'color_germany', 'temperature_fridge']
        plugin_names = plugin_manager.get_all_plugin_names()

        assert set(known_plugin_names).issubset(plugin_names), 'All known test plugins are loaded'

        # test get_plugin_obj_by_name() and get_plugin_objs_of_category()

        color_ger = plugin_manager.get_plugin_by_name('color_germany', 'Color')
        color_ger_obj = plugin_manager.get_plugin_obj_by_name('color_germany', 'Color')

        assert id(color_ger.plugin_object) == id(color_ger_obj), 'locate plugin object works'
        assert color_ger.plugin_object.country == 'germany', 'located object field values look good'

        all_color_objs = plugin_manager.get_plugin_objs_of_category('Color')
        assert id(color_ger_obj) == id([obj for obj in all_color_objs if obj.country == 'germany'][0])

        # test that color_german plugin was configured with the main fashion sites

        conf_sites_germany = ['www.big_fashion_site.de', 'www.other_fashion_site.de']

        assert set(conf_sites_germany) == set(color_ger_obj.main_fashion_sites), 'object is configured'

        # test that plugin objects run its logic correctly

        color_obj_de = plugin_manager.get_plugin_obj_by_name('color_germany', 'Color')
        color_obj_es = plugin_manager.get_plugin_obj_by_name('color_spain', 'Color')

        simple_colors_de = ['grey', 'white', 'black']
        simple_colors_es = ['brown', 'yellow', 'blue']

        col_names_de = color_obj_de.get_season_color_names()
        col_names_es = color_obj_es.get_season_color_names()

        assert col_names_de == simple_colors_de
        assert col_names_es == simple_colors_es

        # Test also the logic of temperature plugin object, this simulates a bit more complex logic
        # Temp readings are simulated as a normal distribution centered at -5 and 0.2 sigma (values from config)
        # we spawn the thread that periodically do temp reading, we wait some intervals and then get the avg temp
        # Finally we check that T avg is -5 +- 10 sigmas (see local config)

        temp_fridge = plugin_manager.get_plugin_obj_by_name('temperature_fridge', 'Temperature')
        temp_fridge.start_update()
        time.sleep(temp_fridge.interval * 20)  # we wait for some temp collection to happen
        temp_fridge.stop = True
        tavg = temp_fridge.get_temperature_average()
        # This test is non-deterministic, but probability of failure is super small, so in practice it is ok
        assert abs(-5.0 - tavg) < 0.2 * 10, 'the avg temperature is close to -5'

        # test subpackage dependencies can be resolved
        assert temp_fridge.engine.power_unit == 'Watts'


@patch.dict(os.environ, {'ZMON_PLUGINS': extras_plugin_dir_abs_path() + ':' + simple_plugin_dir_abs_path()})
def test_load_plugins_extras():
    """
    Test is we can load correctly the extra plugins. Also loads builtins and simple test plugins.
    Notice we put two folders to ZMON_PLUGINS env var, separated by ':'
    """
    # reload the plugin
    importlib.reload(plugin_manager)

    # Lets create a category filter that includes our builtin plugin type and 2 types we defines for our tests
    category_filter = {
        'Function': IFunctionFactoryPlugin,
        'Color': IColorPlugin,
        'Temperature': ITemperaturePlugin,
    }

    # init the plugin manager
    plugin_manager.init_plugin_manager(category_filter=category_filter)

    # collect builtins and explore folder in env var, e.g. ZMON_PLUGINS="/path/one:path/two:/path/three"
    plugin_manager.collect_plugins(load_builtins=True, load_env=True, additional_dirs=None)

    # check categories
    all_categories = plugin_manager.get_all_categories()
    seen_categories = plugin_manager.get_loaded_plugins_categories()
    assert set(all_categories) == set(category_filter.keys()), 'All defined categories are stored'

    assert len(seen_categories) >= 2 and set(seen_categories).issubset(set(all_categories))

    # check known test plugins are loaded
    extra_plugins = ['exacrm', 'job_lock', 'nagios', 'snmp', 'mssql']  # non exhaustive list
    known_plugin_names = extra_plugins + ['http', 'color_spain', 'color_germany', 'temperature_fridge']
    plugin_names = plugin_manager.get_all_plugin_names()
    assert set(known_plugin_names).issubset(plugin_names), 'All known test plugins are loaded'

    # check extra plugins
    for name, category in zip(extra_plugins, ['Function'] * len(extra_plugins)):

        p = plugin_manager.get_plugin_by_name(name, category)
        p_obj = plugin_manager.get_plugin_obj_by_name(name, category)
        assert id(p.plugin_object) == id(p_obj), 'locate plugin object works'

        assert p.is_activated is True
        assert p.plugin_object.is_activated == p.is_activated, 'plugin is activated'

        assert isinstance(p_obj, IFunctionFactoryPlugin), 'plugin object is instance of IFunctionFactoryPlugin'

    # test extra plugin are configured according to config file
    assert plugin_manager.get_plugin_obj_by_name('exacrm', 'Function')._exacrm_cluster == '--secret--'


@patch.dict(os.environ, {'ZMON_PLUGINS': simple_plugin_dir_abs_path()})
def test_global_config():
    """
    Test that the plugin can configure it from the global config and that global config
    takes precedence over local config
    """
    # reload the plugin
    importlib.reload(plugin_manager)

    # Lets create a category filter that includes our builtin plugin type and 2 types we defines for our tests
    category_filter = {
        'Function': IFunctionFactoryPlugin,
        'Color': IColorPlugin,
        'Temperature': ITemperaturePlugin,
    }

    # init the plugin manager
    plugin_manager.init_plugin_manager(category_filter=category_filter)

    # inject as global conf to color_german plugin fashion sites different from the local conf
    global_conf = {
        'plugin.color_germany.fashion_sites': 'superfashion.de hypefashion.de',
        'plugin.other_plugin.otherkey': 'this will not be passed to color_germany.configure',
    }

    # collect plugins builtin and explore env_var: ZMON_PLUGINS="/.../tests/plugins/simple_plugins"
    plugin_manager.collect_plugins(load_builtins=True, load_env=True, additional_dirs=None,
                                   global_config=global_conf)

    # test that color_german plugin was configured according to the global conf

    global_conf_sites = ['superfashion.de', 'hypefashion.de']

    color_ger_obj = plugin_manager.get_plugin_obj_by_name('color_germany', 'Color')

    assert set(global_conf_sites) == set(color_ger_obj.main_fashion_sites), 'object is configured'


@patch.dict(os.environ, {'ZMON_PLUGINS': simple_plugin_dir_abs_path()})
def test_load_broken_plugins():
    """
    Test that we fail predictably on bad plugins and check that we propagate in the exception info to where
    the error is coming from, either in the exception message or in its traceback
    """

    for plugin_dir in 'bad_plugin1', 'bad_plugin2', 'bad_plugin3':

        plugin_abs_dir = broken_plugin_dir_abs_path(plugin_dir)

        # reload the plugin
        importlib.reload(plugin_manager)

        # Lets create a category filter that includes our builtin plugin type and 2 types we defines for our tests
        category_filter = {
            'Function': IFunctionFactoryPlugin,
            'Color': IColorPlugin,
            'Temperature': ITemperaturePlugin,
        }

        # init the plugin manager
        plugin_manager.init_plugin_manager(category_filter=category_filter)

        is_raised = False
        our_plugins_words = ['bad_color', 'badcolor', 'badplugin', 'bad_plugin']
        try:
            # collect plugins should fail with our custom fatal exception
            plugin_manager.collect_plugins(load_builtins=True, load_env=True, additional_dirs=[plugin_abs_dir])
        except plugin_manager.PluginError as e:
            is_raised = True
            exec_all_str = (str(e) + traceback.format_exc()).lower()
            assert any(s in exec_all_str for s in our_plugins_words)

        assert is_raised is True


@patch.dict(os.environ, {'ZMON_PLUGINS': simple_plugin_dir_abs_path()})
def test_plugins_unsatisfied_requirements():
    """
    Test that we recognize missing dependencies in requirements.txt files in plugins dirs
    """

    plugin_abs_dir = broken_plugin_dir_abs_path('plugin_dir_with_requirements')

    # reload the plugin
    importlib.reload(plugin_manager)

    # Lets create a category filter that includes our builtin plugin type and 2 types we defines for our tests
    category_filter = {
        'Function': IFunctionFactoryPlugin,
        'Color': IColorPlugin,
        'Temperature': ITemperaturePlugin,
    }

    # init the plugin manager
    plugin_manager.init_plugin_manager(category_filter=category_filter)

    # test that we detect all missing dependencies in requirements.txt

    is_raised = False
    requirements = ('some_impossible_dependency', 'other_impossible_dependency', 'yet_another_dependency')
    try:
        # collect only builtin plugins should fail with our custom fatal exception
        plugin_manager.collect_plugins(load_builtins=True, load_env=True, additional_dirs=[plugin_abs_dir])

    except plugin_manager.PluginError as e:
        is_raised = True
        for miss_dep in requirements:
            assert miss_dep in str(e), 'Missing dependency in requirement file is discovered'

    assert is_raised is True
