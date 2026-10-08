import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:food_checking_mobile/main.dart';

import 'fake_network.dart';

void main() {
  late FakeNetwork network;
  setUp(() {
    network = FakeNetwork();
    HttpOverrides.global = network;
  });
  tearDown(() => HttpOverrides.global = null);

  test('Fixed Russian and French choices never fail over or reuse a stale active door', () async {
    for (final door in [publicApiBase, publicApiFallback]) {
      network.requests.clear();
      network.statuses[Uri.parse(door).host] = 503;
      final api = Api()
        ..configure(
          door,
          automaticFailover: false,
          activeDoor: door == publicApiBase ? publicApiFallback : publicApiBase,
        );
      api.token = 'existing-test-token';
      await expectLater(api.request('GET', '/diary'), throwsException);
      expect(network.requests.map((r) => r.uri.host), [Uri.parse(door).host]);
      expect(
        network.requests.single.headers.values['authorization'],
        'Bearer existing-test-token',
      );
      network.statuses.clear();
    }
  });

  test('Automatic mode fails over, remembers the working door, and respects a new fixed choice', () async {
    network.statuses[Uri.parse(publicApiBase).host] = 503;
    final api = Api();
    await api.request('GET', '/diary');
    expect(network.requests.map((r) => r.uri.host), [
      Uri.parse(publicApiBase).host,
      Uri.parse(publicApiFallback).host,
    ]);
    network.requests.clear();
    await api.request('GET', '/diary');
    expect(network.requests.single.uri.host, Uri.parse(publicApiFallback).host);
    network.requests.clear();
    network.statuses.clear();
    api.configure(publicApiBase, automaticFailover: false);
    await api.request('GET', '/diary');
    expect(network.requests.single.uri.host, Uri.parse(publicApiBase).host);
  });

  test('Authentication errors do not trigger failover and unknown active doors are ignored', () async {
    network.statuses[Uri.parse(publicApiBase).host] = 401;
    final api = Api()
      ..configure(publicApiBase, activeDoor: 'https://untrusted.example');
    await expectLater(api.request('GET', '/diary'), throwsException);
    expect(network.requests.single.uri.host, Uri.parse(publicApiBase).host);
  });
}
