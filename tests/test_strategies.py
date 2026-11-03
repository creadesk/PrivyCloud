'''
_allowed_hosts() filtert Hosts mit nur_superuser=True.
RoundRobinStrategy verteilt die Auswahl gleichmäßig.
RoundRobinStrategy berücksichtigt nur erlaubte Hosts.
Der Round-Robin-Index wird bei ungültigem Cache-Wert zurückgesetzt.
Bei einer leeren Host-Liste wird None zurückgegeben.
LeastLoadStrategy wählt den Host mit der geringsten Last.
LeastLoadStrategy ignoriert Superuser-Hosts.
LeastLoadStrategy funktioniert auch mit normalen Python-Listen.
Bei einer leeren Host-Liste wird None zurückgegeben.
'''


from django.test import TestCase, RequestFactory
from django.contrib.auth.models import User
from django.core.cache import cache

from paas.models import RemoteHost
from paas.strategies import (
    _allowed_hosts,
    TargetSelectionStrategy,
    RoundRobinStrategy,
    LeastLoadStrategy,
)


class TargetSelectionStrategyTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            password="testpassword"
        )

        self.request = RequestFactory().get("/deploy/")

        self.host1 = RemoteHost.objects.create(
            hostname="host1.example.com",
            ip_address="192.168.1.10",
            nur_superuser=False,
            current_load=5.0,
        )

        self.host2 = RemoteHost.objects.create(
            hostname="host2.example.com",
            ip_address="192.168.1.11",
            nur_superuser=False,
            current_load=2.0,
        )

        self.host3 = RemoteHost.objects.create(
            hostname="host3.example.com",
            ip_address="192.168.1.12",
            nur_superuser=True,
            current_load=0.1,
        )

        cache.clear()

    def tearDown(self):
        cache.clear()


class AllowedHostsTests(TargetSelectionStrategyTests):

    def test_filters_superuser_hosts_from_queryset(self):
        """QuerySets dürfen nur normale Hosts zurückgeben."""

        hosts = _allowed_hosts(RemoteHost.objects.all())

        self.assertEqual(len(hosts), 2)
        self.assertIn(self.host1, hosts)
        self.assertIn(self.host2, hosts)
        self.assertNotIn(self.host3, hosts)

    def test_filters_superuser_hosts_from_list(self):
        """Auch normale Python-Listen müssen gefiltert werden."""

        hosts = _allowed_hosts([
            self.host1,
            self.host2,
            self.host3,
        ])

        self.assertEqual(hosts, [self.host1, self.host2])

    def test_empty_host_list(self):
        """Eine leere Liste bleibt leer."""

        self.assertEqual(_allowed_hosts([]), [])

    def test_all_hosts_superuser_only(self):
        """Wenn alle Hosts gesperrt sind, bleibt die Liste leer."""

        hosts = _allowed_hosts([self.host3])

        self.assertEqual(hosts, [])


class RoundRobinStrategyTests(TargetSelectionStrategyTests):

    def setUp(self):
        super().setUp()
        self.strategy = RoundRobinStrategy()

    def test_selects_hosts_in_round_robin_order(self):
        """Hosts werden der Reihe nach ausgewählt."""

        hosts = RemoteHost.objects.all()

        selected1 = self.strategy.select_target(
            self.request, self.user, hosts
        )

        selected2 = self.strategy.select_target(
            self.request, self.user, hosts
        )

        selected3 = self.strategy.select_target(
            self.request, self.user, hosts
        )

        selected4 = self.strategy.select_target(
            self.request, self.user, hosts
        )

        self.assertEqual(selected1, self.host1)
        self.assertEqual(selected2, self.host2)
        self.assertEqual(selected3, self.host1)
        self.assertEqual(selected4, self.host2)

    def test_never_selects_superuser_only_host(self):
        """Superuser-Hosts dürfen niemals ausgewählt werden."""

        hosts = RemoteHost.objects.all()

        for _ in range(10):
            selected = self.strategy.select_target(
                self.request, self.user, hosts
            )

            self.assertNotEqual(selected, self.host3)

    def test_returns_none_when_no_hosts_available(self):
        """Ohne erlaubte Hosts wird None zurückgegeben."""

        result = self.strategy.select_target(
            self.request,
            self.user,
            [self.host3],
        )

        self.assertIsNone(result)

    def test_returns_none_for_empty_queryset(self):
        """Auch ein leeres QuerySet muss funktionieren."""

        result = self.strategy.select_target(
            self.request,
            self.user,
            RemoteHost.objects.none(),
        )

        self.assertIsNone(result)

    def test_resets_index_when_index_out_of_range(self):
        """Ein ungültiger Cache-Index wird zurückgesetzt."""

        cache.set(self.strategy.CACHE_KEY, 99)

        result = self.strategy.select_target(
            self.request,
            self.user,
            [self.host1, self.host2],
        )

        self.assertEqual(result, self.host1)
        self.assertEqual(cache.get(self.strategy.CACHE_KEY), 1)

    def test_resets_index_when_cache_value_is_invalid(self):
        """Ein ungültiger Cache-Datentyp wird zurückgesetzt."""

        cache.set(self.strategy.CACHE_KEY, "invalid")

        result = self.strategy.select_target(
            self.request,
            self.user,
            [self.host1, self.host2],
        )

        self.assertEqual(result, self.host1)

    def test_works_with_python_list(self):
        """Round Robin muss auch mit Listen funktionieren."""

        hosts = [self.host1, self.host2]

        result = self.strategy.select_target(
            self.request,
            self.user,
            hosts,
        )

        self.assertEqual(result, self.host1)


class LeastLoadStrategyTests(TargetSelectionStrategyTests):

    def setUp(self):
        super().setUp()
        self.strategy = LeastLoadStrategy()

    def test_selects_host_with_lowest_load(self):
        """Der Host mit der niedrigsten Last wird ausgewählt."""

        result = self.strategy.select_target(
            self.request,
            self.user,
            RemoteHost.objects.all(),
        )

        self.assertEqual(result, self.host2)

    def test_ignores_superuser_only_hosts(self):
        """Auch bei Least Load werden gesperrte Hosts ignoriert."""

        # host3 hat mit 0.1 die niedrigste Last,
        # darf aber nicht verwendet werden.

        result = self.strategy.select_target(
            self.request,
            self.user,
            RemoteHost.objects.all(),
        )

        self.assertNotEqual(result, self.host3)
        self.assertEqual(result, self.host2)

    def test_works_with_python_list(self):
        """Least Load muss auch mit Python-Listen funktionieren."""

        hosts = [
            self.host1,
            self.host2,
            self.host3,
        ]

        result = self.strategy.select_target(
            self.request,
            self.user,
            hosts,
        )

        self.assertEqual(result, self.host2)

    def test_returns_none_when_no_hosts_available(self):
        """Bei ausschließlich gesperrten Hosts wird None geliefert."""

        result = self.strategy.select_target(
            self.request,
            self.user,
            [self.host3],
        )

        self.assertIsNone(result)

    def test_returns_none_for_empty_queryset(self):
        """Ein leeres QuerySet liefert None."""

        result = self.strategy.select_target(
            self.request,
            self.user,
            RemoteHost.objects.none(),
        )

        self.assertIsNone(result)

    def test_selects_host_with_zero_load(self):
        """Eine Last von 0.0 muss korrekt berücksichtigt werden."""

        self.host1.current_load = 0.0
        self.host1.save()

        result = self.strategy.select_target(
            self.request,
            self.user,
            RemoteHost.objects.filter(nur_superuser=False),
        )

        self.assertEqual(result, self.host1)


class AbstractStrategyTests(TestCase):

    def test_base_strategy_cannot_be_instantiated(self):
        """Die abstrakte Basisklasse darf nicht instanziiert werden."""

        with self.assertRaises(TypeError):
            TargetSelectionStrategy()
