from textual.geometry import Size

from test_app import CSM, settle, sessions  # noqa: F401  (fixture)


async def test_a_missed_resize_is_caught_and_applied(sessions):  # noqa: F811
    app = CSM(sessions)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot)
        app.real_size = lambda: Size(60, 30)  # the pane shrank and Textual never heard
        app.check_size()
        await settle(pilot)
        assert app.screen.size == Size(120, 40)  # one miss could be a resize still being applied
        app.check_size()
        await settle(pilot)
        assert app.screen.size == Size(60, 30)
        app.check_size()  # sizes agree now: nothing more to do
        assert app.size_misses == 0


async def test_no_check_in_tests_or_when_sizes_agree(sessions):  # noqa: F811
    app = CSM(sessions)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot)
        assert app.real_size() is None  # headless: never resizes
        app.check_size()
        app.real_size = lambda: Size(120, 40)
        app.check_size()
        assert app.size_misses == 0 and app.screen.size == Size(120, 40)
