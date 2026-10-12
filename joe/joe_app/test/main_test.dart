import 'package:flutter_test/flutter_test.dart';
import 'package:joe_native/main.dart';

void main() {
  testWidgets('renders Joe Native header and message composer', (tester) async {
    await tester.pumpWidget(const MainApp());

    expect(find.text('Joe Native'), findsAtLeastNWidgets(1));
    expect(find.text('Enviar'), findsOneWidget);
  });
}
