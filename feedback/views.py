from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.views import View

from feedback import notify
from feedback.forms import FeedbackForm
from feedback.models import Feedback


class FeedbackView(LoginRequiredMixin, View):
    """«Обратная связь»: форма сверху, ниже свои обращения со статусом и ответом.

    Список — только свои (user=request.user): обращения других не видны никому,
    кроме администратора в «Админке».
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
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "items": Feedback.objects.filter(user=request.user),
                "nav_active": "profile",
            },
        )
