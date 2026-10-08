import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

class FakeNetwork extends HttpOverrides {
  final requests = <RecordedRequest>[];
  final statuses = <String, int>{};
  Map<String, dynamic>? Function(RecordedRequest)? responder;
  @override
  HttpClient createHttpClient(SecurityContext? context) => _Client(this);
}

class RecordedRequest extends Fake implements HttpClientRequest {
  RecordedRequest(this.network, this.uri, this.method);
  final FakeNetwork network;
  @override
  final Uri uri;
  @override
  final String method;
  @override
  final TestHeaders headers = TestHeaders();
  final body = <int>[];
  @override
  set followRedirects(bool value) {}
  @override
  void add(List<int> data) => body.addAll(data);
  @override
  Future<HttpClientResponse> close() async {
    final data = uri.path.endsWith('/app/version.json')
        ? {
            'versionCode': 9,
            'versionName': '1.0.8',
            'sizeBytes': 100,
            'sha256': 'a' * 64,
            'apkPath': '/app/releases/9-${'a' * 16}/update.bin',
          }
        : {
            'items': [],
            'foods': [],
            'nutrition': [],
            'total_kcal': '0',
            'total_protein': '0',
            'total_fat': '0',
            'total_carbs': '0',
            'missing_macros': {},
            'estimated_macros': {},
            'missing_kcal': 0,
            'queued_count': 0,
            'token': 'new-test-token',
          };
    return _Response(
      network.statuses[uri.host] ?? 200,
      utf8.encode(jsonEncode(network.responder?.call(this) ?? data)),
    );
  }
}

class _Client extends Fake implements HttpClient {
  _Client(this.network);
  final FakeNetwork network;
  @override
  set connectionTimeout(Duration? value) {}
  @override
  Future<HttpClientRequest> openUrl(String method, Uri url) async {
    final request = RecordedRequest(network, url, method);
    network.requests.add(request);
    return request;
  }

  @override
  void close({bool force = false}) {}
}

class TestHeaders extends Fake implements HttpHeaders {
  final values = <String, String>{};
  @override
  void set(String name, Object value, {bool preserveHeaderCase = false}) =>
      values[name.toLowerCase()] = '$value';
  @override
  set contentType(ContentType? value) {}
}

class _Response extends Stream<List<int>> implements HttpClientResponse {
  _Response(this.statusCode, this.bytes);
  @override
  final int statusCode;
  final List<int> bytes;
  @override
  StreamSubscription<List<int>> listen(
    void Function(List<int>)? onData, {
    Function? onError,
    void Function()? onDone,
    bool? cancelOnError,
  }) => Stream.value(bytes).listen(
    onData,
    onError: onError,
    onDone: onDone,
    cancelOnError: cancelOnError,
  );
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}
