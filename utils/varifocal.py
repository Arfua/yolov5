import mmcv
import torch.nn as nn
import torch.nn.functional as F


def reduce_loss(loss, reduction):
    """Reduce loss as specified.
    Args:
        loss (Tensor): Elementwise loss tensor.
        reduction (str): Options are "none", "mean" and "sum".
    Return:
        Tensor: Reduced loss tensor.
    """
    reduction_enum = F._Reduction.get_enum(reduction)
    # none: 0, elementwise_mean:1, sum: 2
    if reduction_enum == 0:
        return loss
    elif reduction_enum == 1:
        return loss.mean()
    elif reduction_enum == 2:
        return loss.sum()


@mmcv.jit(derivate=True, coderize=True)
def weight_reduce_loss(loss, weight=None, reduction='mean', avg_factor=None):
    """Apply element-wise weight and reduce loss.
    Args:
        loss (Tensor): Element-wise loss.
        weight (Tensor): Element-wise weights.
        reduction (str): Same as built-in losses of PyTorch.
        avg_factor (float): Avarage factor when computing the mean of losses.
    Returns:
        Tensor: Processed loss values.
    """
    # if weight is specified, apply element-wise weight
    if weight is not None:
        loss = loss * weight
    # if avg_factor is not specified, just reduce the loss
    if avg_factor is None:
        loss = reduce_loss(loss, reduction)
        return loss
    # if reduction is mean, then average the loss by avg_factor
    if reduction == 'mean':
        loss = loss.sum() / avg_factor
    # if reduction is 'none', then do nothing, otherwise raise an error
    elif reduction != 'none':
        raise ValueError('avg_factor can not be used with reduction="sum"')
    return loss


@mmcv.jit(derivate=True, coderize=True)
def varifocal_loss(
        loss_fcn, pred, target, weight=None, alpha=0.75, gamma=2.0, iou_weighted=True, reduction='mean',
        avg_factor=None):
    """`Varifocal Loss <https://arxiv.org/abs/2008.13367>`_
    Args:
        loss_fcn: basic loss function (defined criteria)
        pred (torch.Tensor): The prediction with shape (N, C), C is the
            number of classes
        target (torch.Tensor): The learning target of the iou-aware
            classification score with shape (N, C), C is the number of classes.
        weight (torch.Tensor, optional): The weight of loss for each
            prediction. Defaults to None.
        alpha (float, optional): A balance factor for the negative part of
            Varifocal Loss, which is different from the alpha of Focal Loss.
            Defaults to 0.75.
        gamma (float, optional): The gamma for calculating the modulating
            factor. Defaults to 2.0.
        iou_weighted (bool, optional): Whether to weight the loss of the
            positive example with the iou target. Defaults to True.
        reduction (str, optional): The method used to reduce the loss into
            a scalar. Defaults to 'mean'. Options are "none", "mean" and
            "sum".
        avg_factor (int, optional): Average factor that is used to average
            the loss. Defaults to None.
    """
    # pred and target should be of the same size
    assert pred.size() == target.size()
    pred_sigmoid = pred.sigmoid()
    target = target.type_as(pred)
    if iou_weighted:
        focal_weight = (
            target * (target > 0.0).float() +
            alpha * (pred_sigmoid - target).abs().pow(gamma) * (target <= 0.0).float())
    else:
        focal_weight = (
            (target > 0.0).float() +
            alpha * (pred_sigmoid - target).abs().pow(gamma) * (target <= 0.0).float())
    loss = loss_fcn(pred, target) * focal_weight
    loss = weight_reduce_loss(loss, weight, reduction, avg_factor)
    return loss


class VarifocalLoss(nn.Module):
    def __init__(
            self, loss_fcn, gamma=2.0, use_sigmoid=True, alpha=0.75, iou_weighted=True, reduction='mean',
            loss_weight=1.0):
        """`Varifocal Loss <https://arxiv.org/abs/2008.13367>`_
        Args:
            loss_fcn: basic loss function (defined criteria)
            use_sigmoid (bool, optional): Whether the prediction is
                used for sigmoid or softmax. Defaults to True.
            alpha (float, optional): A balance factor for the negative part of
                Varifocal Loss, which is different from the alpha of Focal
                Loss. Defaults to 0.75.
            gamma (float, optional): The gamma for calculating the modulating
                factor. Defaults to 2.0.
            iou_weighted (bool, optional): Whether to weight the loss of the
                positive examples with the iou target. Defaults to True.
            reduction (str, optional): The method used to reduce the loss into
                a scalar. Defaults to 'mean'. Options are "none", "mean" and
                "sum".
            loss_weight (float, optional): Weight of loss. Defaults to 1.0.
        """
        super(VarifocalLoss, self).__init__()
        assert use_sigmoid is True, "Only sigmoid varifocal loss supported now."
        assert alpha >= 0.0
        self.use_sigmoid = use_sigmoid
        self.alpha = alpha
        self.gamma = gamma
        self.iou_weighted = iou_weighted
        self.loss_weight = loss_weight
        self.reduction = reduction
        self.loss_fcn = loss_fcn

    def forward(self, pred, target, weight=None, avg_factor=None, reduction_override=None):
        """Forward function.
        Args:
            pred (torch.Tensor): The prediction.
            target (torch.Tensor): The learning target of the prediction.
            weight (torch.Tensor, optional): The weight of loss for each
                prediction. Defaults to None.
            avg_factor (int, optional): Average factor that is used to average
                the loss. Defaults to None.
            reduction_override (str, optional): The reduction method used to
                override the original reduction method of the loss.
                Options are "none", "mean" and "sum".
        Returns:
            torch.Tensor: The calculated loss
        """
        assert reduction_override in (None, 'none', 'mean', 'sum')
        reduction = reduction_override if reduction_override else self.reduction
        if self.use_sigmoid:
            loss_cls = self.loss_weight * varifocal_loss(
                self.loss_fcn, pred, target, weight, alpha=self.alpha, gamma=self.gamma,
                iou_weighted=self.iou_weighted, reduction=reduction, avg_factor=avg_factor)
        else:
            raise NotImplementedError("Other than sigmoid versions are not supported")
        return loss_cls