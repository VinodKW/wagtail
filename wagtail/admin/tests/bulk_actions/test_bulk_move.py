from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import constants as message_constants
from django.http import HttpRequest, HttpResponse
from django.test import TestCase
from django.urls import reverse

from wagtail.core.models import Page
from wagtail.core.signals import post_page_move, pre_page_move
from wagtail.tests.testapp.models import SimplePage
from wagtail.tests.utils import WagtailTestUtils


class TestBulkMove(TestCase, WagtailTestUtils):
    fixtures = ['test.json']

    def setUp(self):
        # Find root page
        self.root_page = Page.objects.get(id=2)

        # Create three sections
        self.section_a = SimplePage(title="Section A", slug="section-a", content="hello")
        self.root_page.add_child(instance=self.section_a)

        self.section_b = SimplePage(title="Section B", slug="section-b", content="hello")
        self.root_page.add_child(instance=self.section_b)

        self.section_c = SimplePage(title="Section C", slug="section-c", content="hello")
        self.root_page.add_child(instance=self.section_c)

        # Add test page A into section A
        self.test_page_a = SimplePage(title="Hello world!", slug="hello-world-a", content="hello")
        self.section_a.add_child(instance=self.test_page_a)

        # Add test page B into section C
        self.test_page_b = SimplePage(title="Hello world!", slug="hello-world-b", content="hello")
        self.section_c.add_child(instance=self.test_page_b)

        # Add test page B_1 into section C
        self.test_page_b_1 = SimplePage(title="Hello world!", slug="hello-world-b-1", content="hello")
        self.section_c.add_child(instance=self.test_page_b_1)

        # Add test page A_1 into section C having same slug as test page A
        self.test_page_a_1 = SimplePage(title="Hello world!", slug="hello-world-a", content="hello")
        self.section_c.add_child(instance=self.test_page_a_1)

        # Add unpublished page to the root with a child page
        self.unpublished_page = SimplePage(title="Unpublished", slug="unpublished", content="hello")
        sub_page = SimplePage(title="Sub Page", slug="sub-page", content="child")
        self.root_page.add_child(instance=self.unpublished_page)
        self.unpublished_page.add_child(instance=sub_page)

        # unpublish pages last (used to validate the edit only permission)
        self.unpublished_page.unpublish()
        sub_page.unpublish()

        self.url = reverse('wagtailadmin_bulk_move', args=(self.section_c.id, )) + f'?id={self.test_page_b.id}&id={self.test_page_b_1.id}'
        self.confirm_url = reverse('wagtailadmin_bulk_move_confirm', args=(self.section_c.id, self.section_b.id)) + f'?id={self.test_page_b.id}&id={self.test_page_b_1.id}'

        # Login
        self.user = self.login()

    def test_page_move(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_page_move_bad_permissions(self):
        # Remove privileges from user
        self.user.is_superuser = False
        self.user.user_permissions.add(
            Permission.objects.get(content_type__app_label='wagtailadmin', codename='access_admin')
        )
        self.user.save()

        # Get move page
        response = self.client.get(self.url)

        # Check that the user received a 302 redirected response
        self.assertEqual(response.status_code, 302)

    def test_user_without_bulk_delete_permission_can_move(self):
        # to verify that a user without bulk delete permission is able to move a page with a child page

        self.client.logout()
        user = get_user_model().objects.get(email='siteeditor@example.com')
        self.login(user)

        # ensure the bulk_delete is not applicable to this user
        can_bulk_delete = self.test_page_b.permissions_for_user(user).can_delete()
        self.assertFalse(can_bulk_delete)

        response = self.client.get(
            reverse('wagtailadmin_bulk_move', args=(self.root_page.id, )) + f'?id={self.unpublished_page.id}'
        )

        self.assertEqual(response.status_code, 200)

    def test_page_move_confirm(self):
        response = self.client.get(reverse('wagtailadmin_bulk_move_confirm', args=(self.section_c.id, self.section_a.id)) + f'?id={self.test_page_a_1.id}')
        # Duplicate slugs triggers a redirect with an error message.
        self.assertEqual(response.status_code, 302)

        response = self.client.get(reverse('wagtailadmin_home'))
        messages = list(response.context['messages'])
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].level, message_constants.ERROR)
        # Slug should be in error message.
        self.assertIn("{}".format(self.test_page_a_1.slug), messages[0].message)

    def test_move_triggers_signals(self):
        # Connect a mock signal handler to pre_page_move and post_page_move signals
        pre_moved_handler = mock.MagicMock()
        post_moved_handler = mock.MagicMock()

        pre_page_move.connect(pre_moved_handler)
        post_page_move.connect(post_moved_handler)

        # Post to view confirm move page
        try:
            self.client.post(self.confirm_url)
        finally:
            # Disconnect mock handler to prevent cross-test pollution
            pre_page_move.disconnect(pre_moved_handler)
            post_page_move.disconnect(post_moved_handler)

        # Check that the pre_page_move signals were fired
        self.assertTrue(pre_moved_handler.mock_calls[0].called_with(
            sender=self.test_page_b.specific_class,
            instance=self.test_page_b,
            parent_page_before=self.section_c,
            parent_page_after=self.section_b,
            url_path_before='/home/section-c/hello-world-b/',
            url_path_after='/home/section-b/hello-world-b/',
        ))
        self.assertTrue(pre_moved_handler.mock_calls[1].called_with(
            sender=self.test_page_b_1.specific_class,
            instance=self.test_page_b_1,
            parent_page_before=self.section_c,
            parent_page_after=self.section_b,
            url_path_before='/home/section-c/hello-world-b-1/',
            url_path_after='/home/section-b/hello-world-b-1/',
        ))

        # Check that the post_page_move signals were fired
        self.assertTrue(post_moved_handler.mock_calls[0].called_with(
            sender=self.test_page_b.specific_class,
            instance=self.test_page_b,
            parent_page_before=self.section_c,
            parent_page_after=self.section_b,
            url_path_before='/home/section-c/hello-world-b/',
            url_path_after='/home/section-b/hello-world-b/',
        ))
        self.assertTrue(post_moved_handler.mock_calls[1].called_with(
            sender=self.test_page_b_1.specific_class,
            instance=self.test_page_b_1,
            parent_page_before=self.section_c,
            parent_page_after=self.section_b,
            url_path_before='/home/section-c/hello-world-b-1/',
            url_path_after='/home/section-b/hello-world-b-1/',
        ))

    def test_before_move_page_hook(self):
        def hook_func(request, page, destination):
            self.assertIsInstance(request, HttpRequest)
            self.assertIsInstance(page.specific, SimplePage)
            self.assertIsInstance(destination.specific, SimplePage)

        with self.register_hook('before_move_page', hook_func):
            response = self.client.get(self.confirm_url)

        self.assertEqual(response.status_code, 200)

    def test_before_move_page_hook_post(self):
        def hook_func(request, page, destination):
            self.assertIsInstance(request, HttpRequest)
            self.assertIsInstance(page.specific, SimplePage)
            self.assertIsInstance(destination.specific, SimplePage)

            return HttpResponse("Overridden!")

        with self.register_hook('before_move_page', hook_func):
            response = self.client.post(self.confirm_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"Overridden!")

        # pages should not be moved
        self.assertEqual(
            Page.objects.get(id=self.test_page_b.id).get_parent().id,
            self.section_c.id
        )
        self.assertEqual(
            Page.objects.get(id=self.test_page_b_1.id).get_parent().id,
            self.section_c.id
        )

    def test_after_move_page_hook(self):
        def hook_func(request, page):
            self.assertIsInstance(request, HttpRequest)
            self.assertIsInstance(page.specific, SimplePage)

        with self.register_hook('after_move_page', hook_func):
            response = self.client.post(self.confirm_url)

        self.assertEqual(response.status_code, 302)

        # pages should be moved
        self.assertEqual(
            Page.objects.get(id=self.test_page_b.id).get_parent().id,
            self.section_b.id
        )
        self.assertEqual(
            Page.objects.get(id=self.test_page_b_1.id).get_parent().id,
            self.section_b.id
        )