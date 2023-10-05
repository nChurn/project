from typing import List

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from sqlalchemy import desc, func, select, distinct

from const import PaymentType
from api.articles.schemas import Article
from api.cheques.schemas import Cheque
from api.contragents.schemas import Contragent
from api.projects.schemas import Project
from ws_manager import manager

from datetime import datetime

from database.db import (
    database,
    users_cboxes_relation,
    payments,
    pboxes,
    projects,
    users,
    articles,
    cheques,
    contragents,
)
from functions.helpers import get_filters
from functions.users import raschet

import api.payments.schemas as pay_schemas
import functions.filter_schemas as filter_schemas

import asyncio

router = APIRouter(tags=["payments"])


@router.get("/payments", response_model=pay_schemas.GetPaymentsBasic)
async def read_payments_list(
    token: str,
    filters: filter_schemas.PaymentFiltersQuery = Depends(),
    offset: int = 0,
    limit: int = 100,
    sort: str = "created_at:desc",
    created_by: str = None,
    _with: str = None,
    detail_json: str = "",
):
    """Получение списка платежей (с фильтрами или без)"""

    async def _get_pays_list(pays_db):
        payments_list = []
        for pay in pays_db:
            pay_dict = dict(pay)
            pay_dict["repeat"] = {
                "repeat_parent_id": pay_dict["repeat_parent_id"],
                "repeat_period": pay_dict["repeat_period"],
                "repeat_weekday": pay_dict["repeat_weekday"],
                "repeat_day": pay_dict["repeat_day"],
                "repeat_month": pay_dict["repeat_month"],
                "repeat_first": pay_dict["repeat_first"],
                "repeat_last": pay_dict["repeat_last"],
                "repeat_seconds": pay_dict["repeat_seconds"],
                "repeat_number": pay_dict["repeat_number"],
            }
            if _with:
                user_created_by_query = users.select(users.c.id == pay.account)
                user_created_by = await database.fetch_one(user_created_by_query)

                created_by_user = {
                    "user_id": user_created_by.id,
                    "user_name": f"{user_created_by.first_name or ''} {user_created_by.last_name or ''}",
                }
                pay_dict.update({"created_by": created_by_user})

            for key in ["cashbox", "account", "is_deleted"]:
                try:
                    del pay_dict[key]
                except KeyError:
                    pass

            for detail_num in set(detail_json.split(",")):
                if not detail_num:
                    continue
                try:
                    int(detail_num)
                except ValueError:
                    raise HTTPException(
                        400,
                        "detail_json parameter should be integers delimited by comma",
                    )
                if detail_num == "1" and pay_dict["project_id"]:
                    query = projects.select().where(
                        projects.c.id == pay_dict["project_id"],
                        projects.c.cashbox == user.cashbox_id,
                    )
                    project_db = await database.fetch_one(query)
                    if project_db:
                        pay_dict["project_info"] = Project(**dict(project_db)).dict()
                elif detail_num == "2" and pay_dict["article_id"]:
                    query = articles.select().where(
                        articles.c.id == pay_dict["article_id"],
                        articles.c.cashbox == user.cashbox_id,
                    )
                    article_db = await database.fetch_one(query)
                    if article_db:
                        pay_dict["article_info"] = Article(**dict(article_db)).dict()
                elif detail_num == "3" and pay_dict["contragent"]:
                    query = contragents.select().where(
                        contragents.c.id == pay_dict["contragent"],
                        contragents.c.cashbox == user.cashbox_id,
                    )
                    contragent_db = await database.fetch_one(query)
                    if contragent_db:
                        pay_dict["contragent_info"] = Contragent(
                            **dict(contragent_db)
                        ).dict()
                elif detail_num == "4":
                    pay_dict["user_info"] = dict(user)
                elif detail_num == "5" and pay_dict["cheque"]:
                    query = cheques.select().where(
                        cheques.c.id == pay_dict["cheque"],
                        cheques.c.cashbox == user.cashbox_id,
                    )
                    cheque_db = await database.fetch_one(query)
                    if cheque_db:
                        pay_dict["cheque_info"] = Cheque(**dict(cheque_db)).dict()

            payments_list.append(pay_dict)
        return payments_list

    query = users_cboxes_relation.select(users_cboxes_relation.c.token == token)
    user = await database.fetch_one(query)
    filters = get_filters(payments, filters)
    if not user or not user.status:
        raise HTTPException(status_code=403, detail="Вы ввели некорректный токен!")
    sort_list = sort.split(":")
    if not (
        sort_list[1] in ["desc", "asc"]
        and sort_list[0] in ["created_at", "updated_at", "date"]
    ):
        raise HTTPException(
            status_code=400, detail="Вы ввели некорректный параметр сортировки!"
        )
    order_by_type = sort_list[1].upper()

    if created_by:
        created_by_param = created_by.split(",")
        total_pay_list = []
        for cb_id in set(created_by_param):
            try:
                int(cb_id)
            except ValueError:
                error_msg = "created_by parameter should be integers delimited by comma"
                return {"result": None, "count": 0, "errors": {"message": error_msg}}
            created_by_user_query = users_cboxes_relation.select(
                users_cboxes_relation.c.user == int(cb_id)
            )
            created_by_user = await database.fetch_one(created_by_user_query)
            if not created_by_user:
                not_found = f"Пользователь с id={cb_id} не найден"
                return {"result": None, "count": 0, "errors": {"message": not_found}}
            query = f"""
                SELECT *,
                (CASE WHEN payments.contragent IS NULL THEN NULL ELSE (SELECT contragents.name FROM contragents 
                WHERE contragents.id = payments.contragent) END) AS contragent_name
                FROM payments WHERE payments.cashbox = {created_by_user.cashbox_id} AND payments.is_deleted = false AND 
                payments.parent_id IS NULL {filters} ORDER BY payments.{sort_list[0]} {order_by_type} LIMIT {limit} OFFSET {offset};"""
            pays = await database.fetch_all(query)

            pays_list = await _get_pays_list(pays)

            total_pay_list.extend(pays_list)


        c = f"SELECT count(*) FROM payments WHERE payments.cashbox = {created_by_user.cashbox_id} AND payments.is_deleted = false AND payments.parent_id IS NULL {filters}"
        count = await database.fetch_one(c)

        return {"result": total_pay_list, "count": dict(count)['count']} # LOCAL
    
        # return {"result": pays_list, "count": count.count} # PROD

    else:
        query = f"""
            SELECT *,
            (CASE WHEN payments.contragent IS NULL THEN NULL ELSE (SELECT contragents.name FROM contragents 
            WHERE contragents.id = payments.contragent) END) AS contragent_name
            FROM payments WHERE payments.cashbox = {user.cashbox_id} AND payments.is_deleted = false AND 
            payments.parent_id IS NULL {filters} ORDER BY payments.{sort_list[0]} {order_by_type} LIMIT {limit} OFFSET {offset};"""
        pays = await database.fetch_all(query)

        pays_list = await _get_pays_list(pays)

        c = f"SELECT count(*) FROM payments WHERE payments.cashbox = {user.cashbox_id} AND payments.is_deleted = false AND payments.parent_id IS NULL {filters}"
        count = await database.fetch_one(c)

        return {"result": pays_list, "count": dict(count)['count']} # LOCAL
    
        # return {"result": pays_list, "count": count.count} # PROD


@router.post("/payments")
async def create_payment(token: str, payment: pay_schemas.PaymentCreate):
    """Создание платежа"""
    query = users_cboxes_relation.select(users_cboxes_relation.c.token == token)
    user = await database.fetch_one(query)

    if user:
        if user.status:

            payment_dict = payment.dict()

            if not payment_dict.get("date"):
                payment_dict['date'] = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

            if payment_dict.get("preamount") and payment_dict.get("percentamount"):
                amount = payment.preamount * payment.percentamount
                amount_without_tax = amount

                payment_dict['amount'] = amount
                payment_dict['amount_without_tax'] = amount_without_tax

                del payment_dict['preamount']
                del payment_dict['percentamount']
            else:
                del payment_dict['preamount']
                del payment_dict['percentamount']


            if payment_dict.get("repeat"):
                payment_dict.update(payment_dict["repeat"])
            del payment_dict["repeat"]

            paybox_q = pboxes.select().where(
                pboxes.c.id == payment.paybox, pboxes.c.cashbox == user.cashbox_id
            )
            paybox = await database.fetch_one(paybox_q)

            if not paybox:
                raise HTTPException(
                    status_code=403,
                    detail="Введенный счет не принадлежит вам или не существует!",
                )
            paybox_dict = dict(paybox)
            project_dict = None

            if payment.project_id:
                project_q = projects.select().where(
                    projects.c.id == payment.project_id,
                    projects.c.cashbox == user.cashbox_id,
                )
                project = await database.fetch_one(project_q)
                project_dict = dict(project)
                payment_dict["project_id"] = project.id
            else:
                payment_dict["project_id"] = None

            if payment.contragent == 0:
                payment_dict["contragent"] = None
            else:
                payment_dict["contragent"] = payment.contragent

            if payment.article_id:
                article_query = articles.select().where(
                    articles.c.cashbox == user.cashbox_id,
                    articles.c.id == payment.article_id,
                )
                article_name = await database.fetch_one(article_query)

                if not article_name:
                    raise HTTPException(
                        status_code=403,
                        detail="Выбранная статья не принадлежит вам или не существует!",
                    )

                payment_dict["article"] = article_name[1]

            if payment.cheque:
                cheque_query = cheques.select().where(
                    cheques.c.cashbox == user.cashbox_id, cheques.c.id == payment.cheque
                )
                cheque = await database.fetch_one(cheque_query)

                if not cheque:
                    raise HTTPException(
                        status_code=403,
                        detail="Выбранный чек не принадлежит вам или не существует!",
                    )

            payment_dict["cashbox"] = user.cashbox_id
            payment_dict["account"] = user.user
            payment_dict["paybox"] = paybox.id
            payment_dict["is_deleted"] = False
            payment_dict["raspilen"] = False
            payment_dict["created_at"] = int(datetime.utcnow().timestamp())
            payment_dict["updated_at"] = int(datetime.utcnow().timestamp())

            paybox_to_id = None

            if payment.type == PaymentType.transfer:
                if payment.status:
                    paybox_to_id = payment.paybox_to
                    paybox_dict["balance"] = round(
                        float(paybox_dict["balance"]) - float(payment_dict['amount']), 2
                    )
                    query = pboxes.select().where(
                        pboxes.c.id == paybox_to_id, pboxes.c.cashbox == user.cashbox_id
                    )
                    pbox_to = await database.fetch_one(query)

                    if not pbox_to:
                        raise HTTPException(
                            status_code=403,
                            detail="Введенный счет не принадлежит вам или не существует!",
                        )

                    pbox_to_dict = dict(pbox_to)
                    pbox_to_dict["balance"] = round(
                        float(pbox_to_dict["balance"]) + float(payment_dict['amount']), 2
                    )
                    paybox_q = (
                        pboxes.update()
                        .where(
                            pboxes.c.id == paybox_to_id,
                            pboxes.c.cashbox == user.cashbox_id,
                        )
                        .values(pbox_to_dict)
                    )
                    await database.execute(paybox_q)

            query = payments.insert(values=payment_dict)
            pay_id = await database.execute(query)

            if payment.project_id:
                if payment.type == PaymentType.incoming:
                    if payment.status:
                        project_dict["incoming"] += payment_dict['amount_without_tax']
                if payment.type == PaymentType.outgoing:
                    if payment.status:
                        project_dict["outgoing"] += payment_dict['amount_without_tax']
                project_q = (
                    projects.update()
                    .where(
                        projects.c.id == payment.project_id,
                        projects.c.cashbox == user.cashbox_id,
                    )
                    .values(project_dict)
                )
                await database.execute(project_q)

                query = projects.select().where(
                    projects.c.id == payment.project_id,
                    projects.c.cashbox == user.cashbox_id,
                )
                project = await database.fetch_one(query)

                if not project:
                    raise HTTPException(
                        status_code=403,
                        detail="Введенный проект не принадлежит вам или не существует!",
                    )

                in_sum = float(project.incoming)
                out_sum = float(project.outgoing)

                if out_sum == 0 and in_sum != 0:
                    project_dict["profitability"] = 100
                elif out_sum == 0 and in_sum == 0:
                    project_dict["profitability"] = 0
                else:
                    project_dict["profitability"] = round(
                        ((in_sum - out_sum) / out_sum) * 100, 2
                    )

                project_dict["updated_at"] = int(datetime.utcnow().timestamp())

                query = (
                    projects.update()
                    .where(
                        projects.c.id == payment.project_id,
                        projects.c.cashbox == user.cashbox_id,
                    )
                    .values(project_dict)
                )
                await database.execute(query)

                await manager.send_message(
                    token,
                    {"action": "edit", "target": "projects", "result": project_dict},
                )

            payment_dict["id"] = pay_id

            # date_ts = int(datetime.timestamp(datetime.combine(payment.date, datetime.min.time())))

            if paybox_dict["balance_date"] <= payment_dict['date']:
                if payment.status:
                    if payment.type == PaymentType.incoming:
                        paybox_dict["balance"] = round(
                            float(paybox_dict["balance"]) + float(payment_dict['amount']), 2
                        )
                    elif payment.type == PaymentType.outgoing:
                        paybox_dict["balance"] = round(
                            float(paybox_dict["balance"]) - float(payment_dict['amount']), 2
                        )

            await manager.send_message(
                token, {"action": "edit", "target": "payboxes", "result": paybox_dict}
            )

            del paybox_dict["id"]

            paybox_q = (
                pboxes.update()
                .where(
                    pboxes.c.id == payment.paybox, pboxes.c.cashbox == user.cashbox_id
                )
                .values(paybox_dict)
            )
            await database.execute(paybox_q)

            query = f"""
            SELECT payments.id, payments.type, payments.name, payments.external_id, payments.article,
            payments.article_id, payments.project_id, payments.tags, payments.amount,
            payments.amount_without_tax, payments.description, payments.date, payments.repeat_freq,
            payments.repeat_parent_id, payments.repeat_period, payments.repeat_first, payments.repeat_last,
            payments.repeat_number, payments.repeat_day, payments.repeat_month, payments.repeat_weekday,
            payments.repeat_seconds, payments.parent_id, payments.stopped, payments.status, payments.tax,
            payments.tax_type, payments.deb_cred, payments.raspilen, payments.contragent, payments.cashbox,
            payments.paybox, payments.paybox_to, payments.account, payments.is_deleted, payments.cheque,
            payments.docs_sales_id, payments.created_at, payments.updated_at, 
            (CASE WHEN payments.contragent IS NULL THEN NULL ELSE (SELECT contragents.name FROM contragents
             WHERE contragents.id = payments.contragent) END) AS contragent_name
            FROM payments WHERE payments.cashbox = {user.cashbox_id} AND payments.id = {pay_id}
            """
            pay_dict = await database.fetch_one(query)

            await manager.send_message(
                token,
                {"action": "create", "target": "payments", "result": dict(pay_dict)},
            )

            return {**payment_dict, **{ "data": { "status": "success" } }}

    raise HTTPException(status_code=403, detail="Вы ввели некорректный токен!")


@router.get("/payment/{id}", response_model=pay_schemas.PaymentInList)
async def read_payment(token: str, id: int):
    """Просмотр платежа по ID"""
    query = users_cboxes_relation.select(users_cboxes_relation.c.token == token)
    user = await database.fetch_one(query)
    if user:
        if user.status:
            query = (
                payments.select()
                .filter(payments.c.cashbox == user.cashbox_id, payments.c.id == id)
                .order_by(desc(payments.c.id))
            )
            pay = await database.fetch_one(query)

            if pay:
                pay_dict = dict(pay)
                pay_dict["repeat"] = {
                    "repeat_parent_id": pay_dict["repeat_parent_id"],
                    "repeat_period": pay_dict["repeat_period"],
                    "repeat_weekday": pay_dict["repeat_weekday"],
                    "repeat_day": pay_dict["repeat_day"],
                    "repeat_month": pay_dict["repeat_month"],
                    "repeat_first": pay_dict["repeat_first"],
                    "repeat_last": pay_dict["repeat_last"],
                    "repeat_seconds": pay_dict["repeat_seconds"],
                    "repeat_number": pay_dict["repeat_number"],
                }

                # creator of payment
                created_by_query = users.select(users.c.id == user.user)
                created_by_obj = await database.fetch_one(created_by_query)
                created_by_user = {
                    "user_id": created_by_obj.id,
                    "user_name": f"{created_by_obj.first_name or ''} {created_by_obj.last_name or ''}",
                }
                pay_dict.update({"created_by": created_by_user})
                return pay_dict
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Платеж не существует или не принадлежит вам!",
                )

    raise HTTPException(status_code=403, detail="Вы ввели некорректный токен!")


@router.put("/payments/{id}", response_model=pay_schemas.PaymentInListWithData)
async def update_payment(
    token: str, id: int, payment: pay_schemas.PaymentEdit, bg_tasks: BackgroundTasks
):
    """Обновление данных платежа по ID

    Repeats:

        repeat_first, repeat last: unix time in seconds
        repeat_period: yearly, monthly, weekly, daily, hourly, seconds

        Required fields:
            yearly: repeat_month, repeat_day
            monthly: repeat_day
            weekly: repeat_weekday (specify with comma numbers of weekdays, eg: monday, tuesday and sunday "0,1,6")
            seconds: repeat_seconds

        to stop repeats set repeat_period to null
    """
    query = users_cboxes_relation.select(users_cboxes_relation.c.token == token)
    user = await database.fetch_one(query)

    if user:
        if user.status:
            payment_q = payments.select().where(
                payments.c.id == id, payments.c.cashbox == user.cashbox_id
            )
            payment_db = await database.fetch_one(payment_q)

            update_dict: dict = payment.dict(exclude_unset=True)
            if update_dict.get("repeat"):
                update_dict.update(update_dict["repeat"])
                del update_dict["repeat"]

            q = (
                payments.update()
                .where(payments.c.id == id, payments.c.cashbox == user.cashbox_id)
                .values({"updated_at": int(datetime.utcnow().timestamp())})
            )
            await database.execute(q)

            await manager.send_message(
                token,
                {"action": "edit", "target": "payments", "result": dict(payment_db)},
            )

            if update_dict:

                if "paybox" in update_dict and update_dict["paybox"]:
                    q = pboxes.select().where(
                        pboxes.c.cashbox == user.cashbox_id,
                        pboxes.c.id == update_dict["paybox"],
                    )
                    res = await database.fetch_one(q)

                    if not res:
                        raise HTTPException(
                            status_code=403,
                            detail="Введенный счет не принадлежит вам или не существует!",
                        )

                if "paybox_to" in update_dict and update_dict["paybox_to"]:
                    q = pboxes.select().where(
                        pboxes.c.cashbox == user.cashbox_id,
                        pboxes.c.id == update_dict["paybox_to"],
                    )
                    res = await database.fetch_one(q)

                    if not res:
                        raise HTTPException(
                            status_code=403,
                            detail="Введенный счет не принадлежит вам или не существует!",
                        )

                if "project_id" in update_dict and update_dict["project_id"]:
                    q = projects.select().where(
                        projects.c.cashbox == user.cashbox_id,
                        projects.c.id == update_dict["project_id"],
                    )
                    res = await database.fetch_one(q)

                    if not res:
                        raise HTTPException(
                            status_code=403,
                            detail="Введенный проект не принадлежит вам или не существует!",
                        )

                update_dict["updated_at"] = int(datetime.utcnow().timestamp())
                if "project_id" in update_dict and update_dict["project_id"] == 0:
                    update_dict["project_id"] = None

                if "contragent" in update_dict and update_dict["contragent"] == 0:
                    update_dict["contragent"] = None

                if payment.article_id:
                    article_query = articles.select().where(
                        articles.c.cashbox == user.cashbox_id,
                        articles.c.id == payment.article_id,
                    )
                    article_name = await database.fetch_one(article_query)

                    if not article_name:
                        raise HTTPException(
                            status_code=403,
                            detail="Выбранная статья не принадлежит вам или не существует!",
                        )

                    update_dict["article"] = article_name[1]

                if update_dict.get("cheque"):
                    cheque_query = cheques.select().where(
                        cheques.c.cashbox == user.cashbox_id,
                        cheques.c.id == payment.cheque,
                    )
                    cheque = await database.fetch_one(cheque_query)

                    if not cheque:
                        raise HTTPException(
                            status_code=403,
                            detail="Выбранный чек не принадлежит вам или не существует!",
                        )

                q = (
                    payments.update()
                    .where(payments.c.id == id, payments.c.cashbox == user.cashbox_id)
                    .values(update_dict)
                )
                await database.execute(q)

                payment_q = f"""SELECT payments.id, payments.type, payments.name, payments.external_id, payments.article, payments.article_id, payments.project_id, payments.tags, payments.amount, payments.amount_without_tax, payments.description, payments.date, payments.repeat_freq, payments.repeat_parent_id, payments.repeat_period, payments.repeat_first, payments.repeat_last, payments.repeat_number, payments.repeat_day, payments.repeat_month, payments.repeat_weekday, payments.repeat_seconds, payments.parent_id, payments.stopped, payments.status, payments.tax, payments.tax_type, payments.deb_cred, payments.raspilen, payments.contragent, payments.cashbox, payments.paybox, payments.paybox_to, payments.account, payments.is_deleted, payments.cheque, payments.docs_sales_id, payments.created_at, payments.updated_at,
                (CASE WHEN payments.contragent IS NULL THEN NULL ELSE (SELECT contragents.name FROM contragents WHERE contragents.id = payments.contragent) END) AS contragent_name
                FROM payments WHERE payments.cashbox = {user.cashbox_id} AND payments.id = {id}"""
                upd_payment_db = await database.fetch_one(payment_q)

                if "status" in update_dict and payment_db.raspilen:
                    q = (
                        payments.update()
                        .where(
                            payments.c.parent_id == id,
                            payments.c.cashbox == user.cashbox_id,
                        )
                        .values({"status": update_dict["status"]})
                    )
                    await database.execute(q)

                if "project_id" in update_dict and payment_db.raspilen:
                    q = (
                        payments.update()
                        .where(
                            payments.c.parent_id == id,
                            payments.c.cashbox == user.cashbox_id,
                        )
                        .values({"project_id": None})
                    )
                    await database.execute(q)

                if update_dict.keys() & {
                    "type",
                    "amount",
                    "amount_without_tax",
                    "project_id",
                    "paybox",
                    "paybox_to",
                    "status",
                    "is_deleted",
                    "date",
                }:
                    # bg_tasks.add_task(raschet, user, token)
                    asyncio.create_task(raschet(user, token))

                await manager.send_message(
                    token,
                    {
                        "action": "edit",
                        "target": "payments",
                        "result": dict(upd_payment_db),
                    },
                )
                return {**upd_payment_db,  **{ "data": { "status": "success" } }}

    else:
        raise HTTPException(status_code=403, detail="Вы ввели некорректный токен!")


@router.delete("/payments/{payment_id}")
async def delete_payment(token: str, payment_id: int):
    """Удаление платежа по ID"""
    query = users_cboxes_relation.select(users_cboxes_relation.c.token == token)
    user = await database.fetch_one(query)
    if user:
        if user.status:
            q = payments.select().where(payments.c.id == payment_id)
            payment = await database.fetch_one(q)

            if payment.cashbox == user.cashbox_id and payment.account == user.user:

                paybox_q = pboxes.select().where(pboxes.c.id == payment.paybox)
                paybox = await database.fetch_one(paybox_q)

                paybox_dict = dict(paybox)

                if payment.status:
                    if payment.type == PaymentType.incoming:
                        paybox_dict["balance"] = float(paybox_dict["balance"]) - float(
                            payment.amount
                        )
                    elif payment.type == PaymentType.outgoing:
                        paybox_dict["balance"] = float(paybox_dict["balance"]) + float(
                            payment.amount
                        )
                    elif payment.type == PaymentType.transfer:
                        paybox_dict["balance"] = float(paybox_dict["balance"]) + float(
                            payment.amount
                        )

                        paybox_to_q = pboxes.select().where(
                            pboxes.c.id == payment.paybox_to
                        )
                        paybox_to_rec = await database.fetch_one(paybox_to_q)

                        paybox_to = dict(paybox_to_rec)

                        paybox_to["balance"] = float(paybox_to["balance"]) - float(
                            payment.amount
                        )

                        await manager.send_message(
                            token,
                            {
                                "action": "edit",
                                "target": "payboxes",
                                "result": paybox_to,
                            },
                        )

                        paybox_q = (
                            pboxes.update()
                            .where(pboxes.c.id == payment.paybox_to)
                            .values(paybox_to)
                        )
                        await database.execute(paybox_q)

                    paybox_q = (
                        pboxes.update()
                        .where(pboxes.c.id == payment.paybox)
                        .values(paybox_dict)
                    )
                    await database.execute(paybox_q)

                    await manager.send_message(
                        token,
                        {"action": "edit", "target": "payboxes", "result": paybox_dict},
                    )

                q = (
                    payments.update()
                    .where(payments.c.id == payment_id)
                    .values(
                        is_deleted=True, updated_at=int(datetime.utcnow().timestamp())
                    )
                )
                await database.execute(q)

                if payment.project_id:
                    if payment.status:
                        query = projects.select().where(
                            projects.c.id == payment.project_id
                        )
                        project = await database.fetch_one(query)
                        project_dict = dict(project)

                        if payment.type == PaymentType.incoming:
                            project_dict["incoming"] -= payment.amount_without_tax
                        elif payment.type == PaymentType.outgoing:
                            project_dict["outgoing"] -= payment.amount_without_tax

                        q = payments.select().where(
                            payments.c.project_id == payment.project_id,
                            payments.c.status == True,
                            payments.c.is_deleted == False,
                        )
                        payments_db = await database.fetch_all(q)

                        in_sum = 0
                        out_sum = 0

                        for payment in payments_db:
                            if payment.type == PaymentType.incoming:
                                in_sum += payment.amount_without_tax
                            else:
                                out_sum += payment.amount_without_tax

                        if out_sum == 0 and in_sum != 0:
                            project_dict["profitability"] = 100
                        elif out_sum == 0 and in_sum == 0:
                            project_dict["profitability"] = 0
                        else:
                            project_dict["profitability"] = round(
                                ((in_sum - out_sum) / out_sum) * 100, 2
                            )

                        project_dict["updated_at"] = int(datetime.utcnow().timestamp())

                        await manager.send_message(
                            token,
                            {
                                "action": "edit",
                                "target": "projects",
                                "result": project_dict,
                            },
                        )

                        query = (
                            projects.update()
                            .where(projects.c.id == payment.project_id)
                            .values(project_dict)
                        )
                        await database.execute(query)

                await manager.send_message(
                    token,
                    {"action": "delete", "target": "payments", "result": payment_id},
                )
                return {"result": "success"}

    raise HTTPException(status_code=403, detail="Вы ввели некорректный токен!")


@router.post("/payments_split/{id}")
async def raspil_platezha(
    token: str,
    id: int,
    childs: List[pay_schemas.PaymentChildren],
    bg_tasks: BackgroundTasks,
):
    """Распил платежа по ID"""
    query = users_cboxes_relation.select(users_cboxes_relation.c.token == token)
    user = await database.fetch_one(query)
    if user:
        if user.status:
            query = payments.select().filter(
                payments.c.cashbox == user.cashbox_id,
                payments.c.is_deleted == False,
                payments.c.id == id,
            )
            parent_pay = await database.fetch_one(query)

            up_dict = {"raspilen": True}

            if parent_pay:
                if parent_pay.project_id:
                    up_dict["project_id"] = None
                if not parent_pay.raspilen:
                    q = (
                        payments.update()
                        .where(
                            payments.c.cashbox == user.cashbox_id,
                            payments.c.is_deleted == False,
                            payments.c.id == id,
                        )
                        .values(up_dict)
                    )
                    await database.execute(q)

                    q = payments.select().where(
                        payments.c.cashbox == user.cashbox_id,
                        payments.c.is_deleted == False,
                        payments.c.id == id,
                    )
                    pay = await database.fetch_one(q)

                    await manager.send_message(
                        token,
                        {"action": "edit", "target": "payments", "result": dict(pay)},
                    )

                    for item in childs:
                        child = item.dict()
                        parent_pay_dict = dict(parent_pay)
                        del parent_pay_dict["id"]
                        parent_pay_dict["name"] = parent_pay_dict["name"]
                        parent_pay_dict["amount"] = float(child["amount"])
                        parent_pay_dict["amount_without_tax"] = float(child["amount"])
                        parent_pay_dict["contragent"] = child["ca"]
                        parent_pay_dict["project_id"] = child["project"]
                        parent_pay_dict["parent_id"] = parent_pay.id
                        parent_pay_dict["created_at"] = int(
                            datetime.utcnow().timestamp()
                        )
                        parent_pay_dict["updated_at"] = int(
                            datetime.utcnow().timestamp()
                        )

                        q = payments.insert().values(parent_pay_dict)
                        await database.execute(q)

                    # bg_tasks.add_task(raschet, user, token)
                    asyncio.create_task(raschet(user, token))

    else:
        raise HTTPException(status_code=403, detail="Вы ввели некорректный токен!")


@router.put("/payments_split")
async def update_childs_payments(
    token: str, payments_list: List[pay_schemas.ChildrenEdit], bg_tasks: BackgroundTasks
):
    """Обновление дочерних платежей если родительский распилен"""
    query = users_cboxes_relation.select(users_cboxes_relation.c.token == token)
    user = await database.fetch_one(query)

    if user:
        if user.status:
            for payment in payments_list:
                payment_q = payments.select().where(
                    payments.c.id == payment.id, payments.c.cashbox == user.cashbox_id
                )
                payment_db = await database.fetch_one(payment_q)

                update_dict = {
                    i: j
                    for i, j in payment.dict().items()
                    if getattr(payment_db, i) != j
                }

                if update_dict:

                    update_dict["updated_at"] = int(datetime.utcnow().timestamp())
                    if "project_id" in update_dict and update_dict["project_id"] == 0:
                        update_dict["project_id"] = None

                    if "project_id" in update_dict and update_dict["project_id"] != 0:
                        payment_q = (
                            payments.update()
                            .where(
                                payments.c.id == payment_db.parent_id,
                                payments.c.cashbox == user.cashbox_id,
                            )
                            .values({"project_id": None})
                        )
                        await database.execute(payment_q)

                    if "contragent" in update_dict and update_dict["contragent"] == 0:
                        update_dict["contragent"] = None

                    q = (
                        payments.update()
                        .where(
                            payments.c.id == payment.id,
                            payments.c.cashbox == user.cashbox_id,
                        )
                        .values(update_dict)
                    )
                    await database.execute(q)

            # bg_tasks.add_task(raschet, user, token)
            asyncio.create_task(raschet(user, token))

    else:
        raise HTTPException(status_code=403, detail="Вы ввели некорректный токен!")


@router.delete("/payments_split/{id}", response_model=pay_schemas.PaymentInList)
async def split_payment(token: str, id: int, bg_tasks: BackgroundTasks):
    """Отмена распила платежа"""
    query = users_cboxes_relation.select(users_cboxes_relation.c.token == token)
    user = await database.fetch_one(query)

    if user:
        if user.status:
            payment_q = payments.select().where(
                payments.c.id == id, payments.c.cashbox == user.cashbox_id
            )
            payment_db = await database.fetch_one(payment_q)

            if payment_db:

                if payment_db.raspilen:
                    payment_q = (
                        payments.update()
                        .where(
                            payments.c.id == id, payments.c.cashbox == user.cashbox_id
                        )
                        .values({"raspilen": False})
                    )
                    await database.execute(payment_q)

                    payment_q = (
                        payments.update()
                        .where(payments.c.parent_id == id)
                        .values({"is_deleted": True})
                    )
                    await database.execute(payment_q)

                    q = payments.select().where(
                        payments.c.id == id, payments.c.cashbox == user.cashbox_id
                    )
                    pay = await database.fetch_one(q)

                    await manager.send_message(
                        token,
                        {"action": "edit", "target": "payments", "result": dict(pay)},
                    )

                    # bg_tasks.add_task(raschet, user, token)
                    asyncio.create_task(raschet(user, token))

    else:
        raise HTTPException(status_code=403, detail="Вы ввели некорректный токен!")


@router.get("/payments/{id}/childs", response_model=pay_schemas.GetPayments)
async def read_payments_childs(token: str, id: int, offset: int = 0, limit: int = 100):
    """Получение дочерних платежей по ID родительского"""
    query = users_cboxes_relation.select(users_cboxes_relation.c.token == token)
    user = await database.fetch_one(query)
    if user:
        if user.status:
            query = (
                payments.select()
                .filter(payments.c.cashbox == user.cashbox_id)
                .filter(payments.c.is_deleted == False)
                .filter(payments.c.parent_id == id)
                .order_by(desc(payments.c.id))
            )
            pays = await database.fetch_all(query.offset(offset).limit(limit))

            if pays:
                pays_list = [dict(pay) for pay in pays]

                c = (
                    select(func.count(distinct(payments.c.id)))
                    .filter(payments.c.is_deleted == False)
                    .filter(payments.c.cashbox == user.cashbox_id)
                    .filter(payments.c.parent_id == id)
                )
                count = await database.fetch_one(c)

                return {"result": pays_list, "count": count.count_1}
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Вы ввели несуществующий платеж или он не распилен или он не принадлежит вам!",
                )
    raise HTTPException(status_code=403, detail="Вы ввели некорректный токен!")


@router.get("/payments_meta")
async def read_payments_meta(token: str, limit: int = 100, offset: int = 0):
    """Мета платежей"""
    query = users_cboxes_relation.select(users_cboxes_relation.c.token == token)
    user = await database.fetch_one(query)
    if user:
        if user.status:
            q = "SELECT DISTINCT payments.name, payments.tags, payments.article FROM payments WHERE payments.cashbox = :cashbox LIMIT :limit OFFSET :offset"
            payment = await database.fetch_all(
                q, {"cashbox": user.cashbox_id, "limit": limit, "offset": offset}
            )

            return payment

    raise HTTPException(status_code=403, detail="Вы ввели некорректный токен!")


@router.put("/payments/{id}/attachmen", response_model=pay_schemas.PaymentInList)
async def attach_payment(
    token: str, id: int, sale_id: int
):
    """Прикрепление платежа к документу продажи"""

    query = users_cboxes_relation.select(users_cboxes_relation.c.token == token)
    user = await database.fetch_one(query)

    if not user or not user.status:
        raise HTTPException(status_code=403, detail="Вы ввели некорректный токен!")

    query = (
        payments.update()
        .where(payments.c.id == id, payments.c.cashbox == user.cashbox_id)
        .values(
            {"docs_sales_id": sale_id, "updated_at": int(datetime.utcnow().timestamp())}
        )
    )
    await database.execute(query)

    query = payments.select().where(
        payments.c.id == id, payments.c.cashbox == user.cashbox_id
    )
    payment_db = await database.fetch_one(query)

    await manager.send_message(
        token,
        {"action": "edit", "target": "payments", "result": dict(payment_db)},
    )

    return payment_db


@router.delete("/payments/{id}/attachment", response_model=pay_schemas.PaymentInList)
async def detach_payment(token: str, id: int, sale_id: int):
    """Открепление платежа от документа продажи"""

    query = users_cboxes_relation.select(users_cboxes_relation.c.token == token)
    user = await database.fetch_one(query)

    if not user or not user.status:
        raise HTTPException(status_code=403, detail="Вы ввели некорректный токен!")

    query = (
        payments.update()
        .where(payments.c.id == id, payments.c.cashbox == user.cashbox_id)
        .values(
            {"docs_sales_id": None, "updated_at": int(datetime.utcnow().timestamp())}
        )
    )
    await database.execute(query)

    query = payments.select().where(
        payments.c.id == id, payments.c.cashbox == user.cashbox_id
    )
    payment_db = await database.fetch_one(query)

    await manager.send_message(
        token,
        {"action": "edit", "target": "payments", "result": dict(payment_db)},
    )

    return payment_db
