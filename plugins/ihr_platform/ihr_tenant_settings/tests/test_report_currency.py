"""Report template filter `currency`: Indian grouping, paise rounding (P1-T7b).

Guards our patch of care/emr/reports/renderer/template_engine.py against an
upstream sync quietly undoing it.
"""

from decimal import Decimal

from care.emr.reports.renderer.template_engine import TemplateEngine
from django.test import SimpleTestCase

currency = TemplateEngine._filter_currency


class ReportCurrencyFilterTests(SimpleTestCase):
    def test_rounds_paise_half_up_before_splitting(self):
        # Upstream printed "₹1.100" for both of these.
        self.assertEqual(currency("1.995"), "₹2.00")
        self.assertEqual(currency("1.996"), "₹2.00")
        self.assertEqual(currency("1.994"), "₹1.99")
        self.assertEqual(currency("999.995"), "₹1,000.00")
        self.assertEqual(currency(1.995), "₹2.00")  # float goes via str()

    def test_tiny_amounts_and_zero(self):
        self.assertEqual(currency("0.004"), "₹0.00")
        self.assertEqual(currency("0.005"), "₹0.01")
        self.assertEqual(currency(0), "₹0.00")
        self.assertEqual(currency("-0.001"), "₹0.00")

    def test_indian_grouping_with_two_decimals(self):
        self.assertEqual(currency(100000), "₹1,00,000.00")
        self.assertEqual(currency("1234567.5"), "₹12,34,567.50")
        self.assertEqual(currency(Decimal("12345678.50")), "₹1,23,45,678.50")
        self.assertEqual(currency("100000.000000"), "₹1,00,000.00")
        self.assertEqual(currency(999), "₹999.00")

    def test_negative_amounts(self):
        self.assertEqual(currency("-2500.5"), "-₹2,500.50")
        self.assertEqual(currency(-100000), "-₹1,00,000.00")

    def test_empty_and_unreadable_values(self):
        self.assertEqual(currency(None), "")
        self.assertEqual(currency(""), "")
        self.assertEqual(currency("abc"), "abc")
        self.assertEqual(currency("NaN"), "NaN")
        self.assertEqual(currency("Infinity"), "Infinity")

    def test_custom_symbol(self):
        self.assertEqual(currency("100000", "Rs. "), "Rs. 1,00,000.00")

    def test_filter_in_a_template(self):
        env = TemplateEngine().env
        template = env.from_string("{{ amount|currency }}")
        self.assertEqual(template.render(amount="150000.255"), "₹1,50,000.26")
