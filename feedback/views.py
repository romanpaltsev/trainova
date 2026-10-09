from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from feedback import notify
from feedback.forms import FeedbackForm, FeedbackMessageForm
from feedback.models import Feedback


class FeedbackView(LoginRequiredMixin, View):
    """«Обратная связь»: форма сверху, ниже свои обращения строками.

    Список — только свои (user=request.user): обращения других не видны никому,
    кроме администратора в «Админке». Переписка — на странице обращения.
    """

    template_name = "feedback/feedback.html"

    def get(self, request):
        return self.page(request, FeedbackForm(user=request.user))

    def post(self, request):
        form = FeedbackForm(request.POST, user=request.user)
        if not form.is_valid():
            return self.page(request, form)
        feedback = form.save()
        notify.new_feedback(request, feedback)
        messages.success(request, "Спасибо! Сообщение отправлено — ответ появится здесь.")
        # Редирект, а не страница в ответ на POST: F5 не отправит обращение второй раз.
        return redirect("feedback")

    def page(self, request, form):
        # Число ответов — аннотацией в том же запросе, а не запросом на строку.
        items = Feedback.objects.filter(user=request.user).annotate(
            replies=Count("messages", filter=Q(messages__from_admin=True))
        )
        return render(
            request,
            self.template_name,
            {"form": form, "items": items, "nav_active": "profile"},
        )


class FeedbackDetailView(LoginRequiredMixin, View):
    """Обращение и переписка по нему; внизу — «Написать ещё».

    Дописка автора возвращает обращению статус «Новое»: так администратор увидит
    его в счётчике и фильтре, даже если уже закрыл.
    """

    template_name = "feedback/feedback_detail.html"

    def get(self, request, pk):
        item = self.get_object(request, pk)
        return self.page(request, item, FeedbackMessageForm(feedback=item))

    def post(self, request, pk):
        item = self.get_object(request, pk)
        form = FeedbackMessageForm(request.POST, feedback=item)
        if not form.is_valid():
            return self.page(request, item, form)
        message = form.save()
        Feedback.objects.filter(pk=item.pk).update(status=Feedback.Status.NEW)
        notify.followup(request, message)
        messages.success(request, "Отправлено.")
        return redirect("feedback_detail", pk=item.pk)

    def get_object(self, request, pk):
        return get_object_or_404(Feedback, pk=pk, user=request.user)

    def page(self, request, item, form):
        return render(
            request,
            self.template_name,
            {
                "item": item,
                "thread": item.messages.all(),
                "form": form,
                "nav_active": "profile",
            },
        )
