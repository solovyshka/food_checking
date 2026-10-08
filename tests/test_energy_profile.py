"""Resting/training arithmetic, birthday, dated weights and legacy compatibility."""
from datetime import date
from decimal import Decimal
import unittest
from unittest.mock import patch

from tests import test_mobile_api as api_tests
from app.services.energy import age_on


class EnergyProfileTest(unittest.TestCase):
    setUp = api_tests.MobileApiTest.setUp
    tearDown = api_tests.MobileApiTest.tearDown
    diary = api_tests.MobileApiTest.diary
    post = api_tests.MobileApiTest.post
    body = api_tests.MobileApiTest.body
    def profile(self, day, weight='96', height='170'):
        with patch('app.mobile_api.profile_today', return_value=day):
            return self.client.put('/api/mobile/energy/profile',
                json={'weight_kg':weight, 'height_cm':height}, headers=self.headers)

    def test_profile_training_history_and_legacy(self):
        self.assertEqual(age_on(date(2026, 8, 26)), 31)
        self.assertEqual(age_on(date(2026, 8, 27)), 32)
        self.assertEqual(self.profile(date(2026, 10, 3)).json()['resting_kcal'], '1867.5')
        self.assertIsNone(self.diary()['spent_kcal'])
        self.assertEqual(self.diary()['resting_kcal'], '1867.5')
        path = '/api/mobile/days/2026-10-03/activity'
        for _ in range(2):
            self.assertEqual(self.client.put(path, json={'training_kcal':'500.50'}, headers=self.headers).status_code, 200)
        self.assertEqual(self.diary()['spent_kcal'], '2368.00')
        self.assertEqual(self.diary()['energy_delta'], '-2368.0')
        self.assertTrue(self.diary()['energy_delta_estimated'])
        self.post(self.body())
        self.assertEqual(self.diary()['energy_delta'], '-2264.0')
        self.profile(date(2026, 10, 4), '95')
        self.assertEqual(self.diary()['resting_kcal'], '1867.5')
        self.assertEqual(self.diary('2026-10-04')['resting_kcal'], '1857.5')
        self.assertIsNone(self.diary('2026-10-02')['resting_kcal'])
        self.client.put(path, json={'training_kcal':0}, headers=self.headers)
        self.assertEqual(self.diary()['spent_kcal'], '1867.50')
        self.client.put(path, json={'training_kcal':None}, headers=self.headers)
        self.assertIsNone(self.diary()['spent_kcal'])
        self.client.put('/api/mobile/days/2026-10-03/energy', json={'spent_kcal':2500}, headers=self.headers)
        self.assertEqual(self.diary()['energy_mode'], 'legacy_total')
        self.assertEqual(Decimal(self.diary()['spent_kcal']), 2500)
        self.assertIsNone(self.diary()['training_kcal'])
        self.client.put(path, json={'training_kcal':0}, headers=self.headers)
        self.assertEqual(self.diary()['energy_mode'], 'resting_training')
        self.assertEqual(Decimal(self.diary()['spent_kcal']), Decimal('1867.5'))

    def test_validation_auth_missing_profile_and_birthday(self):
        self.assertEqual(self.client.get('/api/mobile/energy/profile').status_code, 401)
        self.assertEqual(self.client.put('/api/mobile/energy/profile', json={'weight_kg':96,'height_cm':170}).status_code, 401)
        path = '/api/mobile/days/2026-10-03/activity'
        self.assertEqual(self.client.put(path, json={'training_kcal':100}).status_code, 401)
        for value in ('NaN', 'Infinity', -1, 100001, '3.141', True):
            self.assertEqual(self.client.put(path, json={'training_kcal':value}, headers=self.headers).status_code, 422)
        self.assertEqual(self.client.put(path, json={}, headers=self.headers).status_code, 422)
        for weight in (0, 401, 'NaN', '96.001', True):
            self.assertEqual(self.profile(date(2026,10,3), weight).status_code, 422)
        self.assertEqual(self.profile(date(2026,10,3), height='99').status_code, 422)
        self.client.put(path, json={'training_kcal':100}, headers=self.headers)
        self.assertIsNone(self.diary()['energy_delta'])
        self.profile(date(2026,8,26))
        self.assertEqual(self.diary('2026-08-26')['resting_kcal'], '1872.5')
        self.assertEqual(self.diary('2026-08-27')['resting_kcal'], '1867.5')

    def test_past_rest_only_delta_preserves_unknown_training(self):
        from app.db.mobile_models import MobileDailyEnergy
        self.profile(date(2026, 10, 3))
        self.post(self.body())
        with self.factory() as db:
            db.add(MobileDailyEnergy(entry_date=date(2026,10,3), source='rest_only'))
            db.commit()
        day = self.diary()
        self.assertEqual(day['energy_delta'], '-1763.5')
        self.assertEqual(day['energy_mode'], 'rest_only')
        self.assertTrue(day['energy_delta_estimated'])
        self.assertTrue(day['training_kcal_missing'])
        self.assertIsNone(day['training_kcal'])
        self.client.put('/api/mobile/days/2026-10-03/activity', json={'training_kcal':500}, headers=self.headers)
        self.assertEqual(self.diary()['energy_delta'], '-2263.5')
        self.assertEqual(self.diary()['energy_mode'], 'resting_training')
        self.assertFalse(self.diary()['training_kcal_missing'])
